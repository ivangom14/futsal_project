"""Manejadores de los trabajos de la cola (T06b). Todas las descargas pasan por el `Fetcher` (ritmo, registro, interruptor).

Cada manejador recibe un `Context` y devuelve el checkpoint del trabajo; lanza `JobError` (transitorio o definitivo) o
`BreakerTripped` (el interruptor se abrió: el trabajo ya está en `pending` sin consumir intento). No hay estado global.

- `discover_catalog`: GET /api/competitions -> informe en el checkpoint -> un `discover_groups` por competición.
- `discover_groups`: GET /api/groups -> informe de `discovery_v2` -> `import_catalog` (metadatos oficiales) -> `calendar` por grupo.
- `calendar`: parsea el calendario con validación de ámbito; NO escribe partidos (no trae estado ni acta): resumen en el checkpoint
  y siembra de `round_results` con el planificador.
- `round_results`: /api/results -> LeagueFile de UNA jornada -> `run_import` con la `RegressionPolicy` por defecto (puertas de la Fase 21).
- `standings` / `scorers`: `table_ingest` con la descarga inyectada a través del `Fetcher`.
"""

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from futsal.db.models import Competition, CompetitionGroup, DataQualityIssue, Match, Season
from futsal.importer.catalog_service import import_catalog
from futsal.importer.schema import LeagueFile, MatchIn, NamedExt, RoundIn
from futsal.importer.service import run_import
from futsal.importer.table_service import ingest_tables
from futsal.ingestion.queue import jobs
from futsal.ingestion.queue.controls import QueueConfig
from futsal.ingestion.queue.fetch import Fetcher, FetchResult
from futsal.ingestion.queue.planner import (
    CALENDAR,
    ROUND_RESULTS,
    SCORERS,
    STANDINGS,
    calendar_summary_rounds,
    collapse,
    gather_state,
    group_target,
    plan_group,
)
from futsal.ingestion.queue.schedule import ScheduleConfig
from futsal.ingestion.rffm.calendar import parse_calendar
from futsal.ingestion.rffm.catalog import CONFIRMED, GROUPS_OK, normalize_name
from futsal.ingestion.rffm.client import (
    FetchError,
    calendar_url,
    competitions_api_url,
    results_api_url,
)
from futsal.ingestion.rffm.discovery_v2 import discover
from futsal.ingestion.rffm.parser import RffmParseError
from futsal.ingestion.rffm.results_api import Rejected, parse_results_api

DISCOVER_CATALOG, DISCOVER_GROUPS = "discover_catalog", "discover_groups"
TZ_NAME = "Europe/Madrid"
TABLE_OK = ("ok", "empty", "incomplete", "not_applicable")


class JobError(Exception):
    """Fallo del trabajo: `transient` -> `failed` con espera exponencial; si no, `dead`."""

    def __init__(self, message: str, *, transient: bool, http_status: int | None = None) -> None:
        super().__init__(message)
        self.transient, self.http_status = transient, http_status


class BreakerTripped(Exception):
    """El interruptor del source se abrió durante el trabajo (el trabajo ya está en `pending`, sin intento consumido)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class JobView:
    id: int
    kind: str
    target: dict[str, Any]
    attempts: int
    run_id: int | None = None


@dataclass
class Context:
    session_factory: Callable[[], Session]
    fetcher: Fetcher
    engine: Engine
    now: Callable[[], datetime]
    cfg: QueueConfig
    schedule: ScheduleConfig
    work_dir: Path
    job: JobView
    source: str = "rffm"

    # ------------------------------------------------------------------ red
    def fetch(self, url: str) -> FetchResult:
        """GET por el `Fetcher`; confirma el registro de la petición y traduce el resultado de transporte."""
        try:
            res = self.fetcher.get(url, self.job.id)
        finally:
            self.fetcher.session.commit()  # raw_fetches, pausa y liberación del trabajo quedan guardados pase lo que pase
        if res.tripped:
            raise BreakerTripped(res.tripped)
        if res.parse_status == "ok":
            return res
        if res.parse_status == "null_body":
            raise JobError(f"cuerpo null o vacío en {url}", transient=True, http_status=res.status_code)
        if res.parse_status == "network_error":
            raise JobError(f"error de red en {url}", transient=True)
        status = res.status_code or 0
        raise JobError(f"HTTP {status} en {url}", transient=status >= 500, http_status=res.status_code)

    def parsed(self, res: FetchResult, status: str, detail: str | None = None) -> None:
        """Informa del resultado de validar la página. `scope_mismatch` abre el interruptor y detiene el trabajo."""
        tripped = self.fetcher.report_parse(res, status, detail, self.job.id)
        self.fetcher.session.commit()
        if status == "scope_mismatch":
            if not tripped:  # defensa: el breaker debe abrirse siempre; si no, se libera y se detiene igualmente
                with self.session_factory() as s, s.begin():
                    jobs.release(s, self.job.id, self.now())
            raise BreakerTripped(tripped or f"scope_mismatch: {detail}")
        if status == "schema_error":
            if tripped:
                raise BreakerTripped(tripped)
            raise JobError(f"estructura inesperada: {detail}", transient=False)

    # ------------------------------------------------------------------ cola
    def enqueue(self, kind: str, target: dict[str, Any], *, priority: int, not_before: datetime | None = None,
                target_key: str | None = None) -> None:
        with self.session_factory() as s, s.begin():
            jobs.enqueue(s, self.source, kind, target, priority=priority, not_before=not_before,
                         max_attempts=self.cfg.max_attempts, run_id=self.job.run_id, target_key=target_key, now=self.now())


Handler = Callable[[Context], dict[str, Any]]


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


# ---------------------------------------------------------------------------------------------------------------- catálogo


def discover_catalog(ctx: Context) -> dict[str, Any]:
    t = ctx.job.target
    season, game_type = str(t["season"]), str(t["game_type"])
    url = competitions_api_url(season, game_type)
    seen: list[FetchResult] = []

    def fetch(u: str) -> str:
        if u != url:  # los grupos se piden en `discover_groups`, uno por competición
            raise FetchError("los grupos se descargan en discover_groups")
        seen.append(ctx.fetch(u))
        return seen[-1].text

    try:
        report = discover(season, game_type, fetch, now=ctx.now)
    except RuntimeError as exc:
        if seen:
            ctx.parsed(seen[0], "schema_error", str(exc))
        raise
    if any(i["code"] == "game_type_mismatch" for i in report["incidents"]):
        ctx.parsed(seen[0], "scope_mismatch", "el catálogo trae competiciones de otro tipo de juego")
    ctx.parsed(seen[0], "ok")
    report["incidents"] = [i for i in report["incidents"] if i["code"] != "groups_unavailable"]
    for c in report["competitions"]:
        c["groups_status"] = "pending"
    pri = ctx.schedule.priorities
    for c in report["competitions"]:
        target = {"season": season, "game_type": game_type, "competition": c["competition_id"]}
        ctx.enqueue(DISCOVER_GROUPS, {**target, "meta": c["competition_meta"]},
                    priority=pri.rest if c["active"] else pri.inactive, target_key=jobs.canonical_key(target))
    report.pop("groups", None)
    return {"report": report, "competitions": len(report["competitions"]), "groups_jobs_enqueued": len(report["competitions"])}


def _names(s: Session, season: str, competition: str) -> tuple[str | None, str | None]:
    season_name = s.scalar(select(Season.name).where(Season.source == "rffm", Season.external_id == season))
    comp_name = s.scalar(select(Competition.name).where(Competition.source == "rffm", Competition.external_id == competition))
    return season_name, comp_name


def discover_groups(ctx: Context) -> dict[str, Any]:
    t = ctx.job.target
    season, game_type, cid, meta = str(t["season"]), str(t["game_type"]), str(t["competition"]), t.get("meta")
    if not isinstance(meta, dict):
        raise JobError("el trabajo no trae los metadatos de la competición (target.meta)", transient=False)
    comp_url = competitions_api_url(season, game_type)
    seen: list[FetchResult] = []

    def fetch(u: str) -> str:
        if u == comp_url:  # el catálogo ya se descargó en discover_catalog: no se repite la petición
            return json.dumps([meta])
        seen.append(ctx.fetch(u))
        return seen[-1].text

    report = discover(season, game_type, fetch, now=ctx.now)
    if not report["competitions"]:
        raise JobError(f"la competición {cid} no es válida para el tipo de juego {game_type}", transient=False)
    if report["competitions"][0]["groups_status"] == "fetch_failed":
        detail = "; ".join(i["detail"] for i in report["incidents"] if i["code"] == "groups_unavailable")
        ctx.parsed(seen[0], "schema_error", detail or "respuesta de grupos inválida")
    ctx.parsed(seen[0], "ok")
    with ctx.session_factory() as s:
        season_name, comp_name = _names(s, season, cid)
        known = set(s.scalars(select(CompetitionGroup.external_id).where(CompetitionGroup.source == "rffm")))
    skeleton_dir = ctx.work_dir / "skeleton"
    skeleton_dir.mkdir(parents=True, exist_ok=True)
    new_groups: list[str] = []
    for g in report["groups"]:
        path = skeleton_dir / f"s{season}_c{cid}_g{g['group_id']}.json"
        league = LeagueFile(source="rffm", season=NamedExt(external_id=season, name=season_name),
                            competition=NamedExt(external_id=cid, name=g["competition_name"] or comp_name),
                            group=NamedExt(external_id=g["group_id"], name=g["group_name"]), rounds=[], matches=[])
        path.write_text(league.model_dump_json(), encoding="utf-8")
        g["status"], g["league_path"] = "downloaded", str(path)
        if g["group_id"] not in known:
            new_groups.append(g["group_id"])
    results = import_catalog(ctx.engine, report)
    failed = [r for r in results if r["import_status"] != "success"]
    if failed:
        raise JobError(f"import_catalog falló en {len(failed)} grupos: {failed[0].get('error')}", transient=True)
    pri = ctx.schedule.priorities
    queued = 0
    for g in report["groups"]:
        if g["active"] or g["group_id"] in new_groups:
            ctx.enqueue(CALENDAR, group_target(season, cid, g["group_id"], game_type),
                        priority=pri.rest if g["active"] else pri.inactive)
            queued += 1
    return {"competition": cid, "groups": [g["group_id"] for g in report["groups"]], "new_groups": new_groups,
            "catalog_meta_changes": {r["group_id"]: r["catalog_meta_changes"] for r in results if r.get("catalog_meta_changes")},
            "calendar_jobs_enqueued": queued, "incidents": report["incidents"]}


# ---------------------------------------------------------------------------------------------------------------- calendario


def summarize_calendar(page: Any) -> dict[str, Any]:
    rounds: list[dict[str, Any]] = []
    for r in page.rounds:
        dates = sorted({m.scheduled_date.isoformat() for m in r.matches if m.scheduled_date})
        rounds.append({
            "code": r.code, "number": r.number, "first_date": dates[0] if dates else None,
            "last_date": dates[-1] if dates else None, "dates": dates,
            "times_known": sum(m.time_known for m in r.matches), "times_empty": sum(not m.time_known for m in r.matches),
            "matches": [{"codacta": m.codacta, "date": m.scheduled_date.isoformat() if m.scheduled_date else None,
                         "time": m.scheduled_time.strftime("%H:%M") if m.scheduled_time else None} for m in r.matches]})
    return {"current_round": page.current_round, "format": page.competition_format, "rounds": rounds,
            "matches": sum(len(r["matches"]) for r in rounds),
            "times_known": sum(r["times_known"] for r in rounds), "times_empty": sum(r["times_empty"] for r in rounds),
            "issues": dict(Counter(i.code for i in page.issues))}


def calendar(ctx: Context) -> dict[str, Any]:
    t = ctx.job.target
    season, comp, group, game_type = str(t["season"]), str(t["competition"]), str(t["group"]), str(t["game_type"])
    res = ctx.fetch(calendar_url(season, comp, group, game_type))
    requested: dict[str, Any] = {"season": season, "game_type": game_type, "competition": comp, "group": group}
    try:
        page = parse_calendar(res.text, requested)
    except RffmParseError as exc:
        ctx.parsed(res, "schema_error", str(exc))
        raise
    if not page.scope.ok:
        ctx.parsed(res, "scope_mismatch", "; ".join(page.scope.mismatches))
    ctx.parsed(res, "ok")
    summary = summarize_calendar(page)
    summary["fetched_at"] = _iso(ctx.now())
    summary["name_mismatches"] = _name_mismatches(ctx, comp, group, page)
    seeded = _seed_rounds(ctx, season, group, game_type, summary)
    return {**summary, "round_jobs_seeded": seeded}


def _name_mismatches(ctx: Context, comp: str, group: str, page: Any) -> list[dict[str, Any]]:
    """Nombres de la página distintos de los de la BD: incidencia `name_mismatch`, nunca interruptor (el ámbito se valida por códigos)."""
    with ctx.session_factory() as s:
        row = s.execute(select(Competition.name, CompetitionGroup.name).join(
            CompetitionGroup, CompetitionGroup.competition_id == Competition.id)
            .where(CompetitionGroup.external_id == group, CompetitionGroup.source == "rffm")).one_or_none()
    found: list[dict[str, Any]] = []
    if row is not None:
        for what, ext, seen, stored in (("competition", comp, page.competition_name, row[0]), ("group", group, page.group_name, row[1])):
            if seen and stored and normalize_name(seen) != normalize_name(stored):
                found.append({"entity": what, "external_id": ext, "page": seen, "db": stored})
    if found and ctx.job.run_id is not None:
        with ctx.session_factory() as s, s.begin():
            for f in found:
                s.add(DataQualityIssue(ingestion_run_id=ctx.job.run_id, code="name_mismatch", entity_type=f["entity"],
                                       external_id=f["external_id"], details=f,
                                       message=f"nombre de la página {f['page']!r} distinto del almacenado {f['db']!r}"))
    return found


def _seed_rounds(ctx: Context, season: str, group: str, game_type: str, summary: dict[str, Any]) -> int:
    """Encola las `round_results` que el planificador considera pendientes para este grupo (carga inicial incluida)."""
    from zoneinfo import ZoneInfo

    with ctx.session_factory() as s:
        state = gather_state(s, "rffm", season, game_type, ctx.schedule, group_ids=[group])
    if not state.groups:
        return 0
    g = replace(state.groups[0], calendar=calendar_summary_rounds(summary, ZoneInfo(ctx.schedule.timezone)))
    specs = collapse(plan_group(g, state.last_done, ctx.schedule, ctx.now(), only_kinds=[ROUND_RESULTS]))
    for spec in specs:
        ctx.enqueue(spec.kind, spec.target, priority=spec.priority, not_before=spec.not_before)
    return len(specs)


# ---------------------------------------------------------------------------------------------------------------- jornada


def _dmy(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value).strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def build_round_league(payload: dict[str, Any], result: Any, target: dict[str, Any], names: tuple[str | None, str | None, str | None]
                       ) -> LeagueFile:
    """LeagueFile de UNA jornada con el formato de `importer/schema.py`. La hora sale de /api/results («00:00» = desconocida)."""
    season_name, comp_name, group_name = names
    url = results_api_url(str(target["group"]), result.round_id)
    dates = [m.match.scheduled_date for m in result.matches if m.match.scheduled_date]
    round_date = _dmy(payload.get("fecha_jornada")) or (min(dates) if dates else None)
    number = int(result.round_id) if result.round_id.isdigit() else None
    rnd = RoundIn(external_round_id=result.round_id, visible_round_label=result.round_name or result.round_id,
                  visible_round_number=number, scheduled_date=round_date, matches=len(result.matches), source_url=url, status="ok")
    matches = []
    for rm in result.matches:
        m = rm.match
        matches.append(MatchIn(
            external_match_id=m.external_match_id, external_round_id=result.round_id,
            home_team_external_id=m.home_team_external_id, home_team_name=m.home_team_name,
            away_team_external_id=m.away_team_external_id, away_team_name=m.away_team_name,
            home_score=m.home_score, away_score=m.away_score, status=m.status, scheduled_date=m.scheduled_date,
            scheduled_time=None if m.scheduled_time == time(0, 0) else m.scheduled_time, timezone=m.timezone, venue=m.venue,
            source_url=url))
    return LeagueFile(source="rffm", season=NamedExt(external_id=str(target["season"]), name=season_name),
                      competition=NamedExt(external_id=str(target["competition"]), name=comp_name),
                      group=NamedExt(external_id=str(target["group"]), name=group_name), rounds=[rnd], matches=matches)


def _statuses(s: Session, codactas: list[str]) -> dict[str, str]:
    return dict(s.execute(select(Match.external_id, Match.status).where(Match.source == "rffm", Match.external_id.in_(codactas))).all())


def round_results(ctx: Context) -> dict[str, Any]:
    t = ctx.job.target
    group, rnd = str(t["group"]), str(t["round"])
    res = ctx.fetch(results_api_url(group, rnd))
    parsed = parse_results_api(res.body, group, rnd)
    if isinstance(parsed, Rejected):
        if parsed.transient:  # `null`: fallo intermitente de la web (el Fetcher ya lo contó para el interruptor)
            raise JobError(f"/api/results devolvió null ({parsed.detail})", transient=True, http_status=res.status_code)
        if parsed.reason in ("group_mismatch", "round_mismatch"):
            ctx.parsed(res, "scope_mismatch", f"{parsed.reason}: {parsed.detail}")
        ctx.parsed(res, "schema_error", f"{parsed.reason}: {parsed.detail}")
        raise JobError(f"{parsed.reason}: {parsed.detail}", transient=False)
    if parsed.competition_id and parsed.competition_id != str(t["competition"]):
        ctx.parsed(res, "scope_mismatch", f"codigo_competicion={parsed.competition_id!r} != {t['competition']!r}")
    ctx.parsed(res, "ok")
    payload = json.loads(res.body)
    with ctx.session_factory() as s:
        row = s.execute(select(Season.name, Competition.name, CompetitionGroup.name, Competition.external_id, Season.external_id,
                               CompetitionGroup.has_standings, CompetitionGroup.has_scorers)
                        .join(Competition, Competition.season_id == Season.id)
                        .join(CompetitionGroup, CompetitionGroup.competition_id == Competition.id)
                        .where(CompetitionGroup.external_id == group, CompetitionGroup.source == "rffm")).one_or_none()
        if row is None:
            raise JobError(f"el grupo {group} no está importado: falta discover_groups", transient=False)
        if (row[3], row[4]) != (str(t["competition"]), str(t["season"])):
            raise JobError(f"el grupo {group} está en la competición {row[3]} / temporada {row[4]}, no en lo pedido", transient=False)
        codactas = [m.match.external_match_id for m in parsed.matches if m.match.external_match_id]
        before = _statuses(s, codactas)
    league = build_round_league(payload, parsed, t, (row[0], row[1], row[2]))
    path = ctx.work_dir / "rounds" / f"g{group}_r{rnd}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(league.model_dump_json(), encoding="utf-8")
    report = run_import(ctx.engine, path)  # RegressionPolicy por defecto: identity_conflict y finished_regression_held activas
    if report.status == "failed":
        raise JobError(f"run_import falló: {report.error}", transient=True)
    with ctx.session_factory() as s:
        after = _statuses(s, codactas)
    new_finished = sorted(c for c, st in after.items() if st == "finished" and before.get(c) != "finished")
    queued: list[str] = []
    if new_finished:
        when = ctx.now() + timedelta(minutes=ctx.schedule.tables_group_minutes)
        target = group_target(str(t["season"]), str(t["competition"]), group, str(t["game_type"]))
        for kind, flag in ((STANDINGS, row[5]), (SCORERS, row[6])):
            if flag is not False:
                ctx.enqueue(kind, target, priority=ctx.schedule.priorities.tables, not_before=when)
                queued.append(kind)
    return {"fetched_at": _iso(ctx.now()), "round": rnd, "matches": len(parsed.matches), "import_status": report.status,
            "issue_codes": dict(report.issue_codes), "new_finished": new_finished, "tables_enqueued": queued,
            "times_known": sum(m.scheduled_time is not None for m in league.matches), "run_id": report.run_id,
            "results_issues": parsed.issues}


# ---------------------------------------------------------------------------------------------------------------- tablas


def _tables(kind: str) -> Handler:
    def handler(ctx: Context) -> dict[str, Any]:
        t = ctx.job.target
        season, comp, group, game_type = str(t["season"]), str(t["competition"]), str(t["group"]), str(t["game_type"])
        with ctx.session_factory() as s:
            row = s.execute(select(Competition.name, CompetitionGroup.name, CompetitionGroup.expected_teams)
                            .join(CompetitionGroup, CompetitionGroup.competition_id == Competition.id)
                            .where(CompetitionGroup.external_id == group, CompetitionGroup.source == "rffm")).one_or_none()
        if row is None:
            raise JobError(f"el grupo {group} no está importado: falta discover_groups", transient=False)
        inventory = {"season": {"external_id": season}, "game_type": game_type, "entries": [{
            "competition": {"external_id": comp, "name": row[0]}, "identification": CONFIRMED, "groups_status": GROUPS_OK,
            "groups": [{"external_id": group, "name": row[1], "expected_teams": row[2]}]}]}
        seen: list[FetchResult] = []

        def fetch(url: str) -> str:
            seen.append(ctx.fetch(url))  # lanza JobError/BreakerTripped: atraviesan `table_ingest` hasta el trabajador
            return seen[-1].text

        rep = ingest_tables(ctx.engine, kind, inventory, ctx.work_dir / "tables", fetch, groups={group}, refresh=True,
                            null_retries=0, requests_made=lambda: ctx.fetcher.requests, now=ctx.now, sleep=lambda _s: None)
        res = rep["groups"][0]
        entry = res["rounds"][0] if res["rounds"] else {}
        outcome = str(entry.get("outcome") or res.get("status"))
        if outcome in TABLE_OK:
            if seen:
                ctx.parsed(seen[-1], "ok")
            return {"kind": kind, "outcome": outcome, "rows": entry.get("rows"), "warnings": entry.get("warnings"),
                    "returned_round": entry.get("returned_round"), "fetched_at": _iso(ctx.now())}
        code, detail = entry.get("error_code"), str(entry.get("error") or res.get("error") or outcome)
        if seen and code == "identity":
            ctx.parsed(seen[-1], "scope_mismatch", detail)
        if seen and code == "structure":
            ctx.parsed(seen[-1], "schema_error", detail)
        raise JobError(f"{kind}: {outcome}: {detail}", transient=code in ("transient", "download", "database"))

    return handler


HANDLERS: dict[str, Handler] = {
    DISCOVER_CATALOG: discover_catalog, DISCOVER_GROUPS: discover_groups, CALENDAR: calendar,
    ROUND_RESULTS: round_results, STANDINGS: _tables("standings"), SCORERS: _tables("scorers"),
}
