"""Orquestación de `refresh`: descarga → barrera de calidad → persistencia → trazabilidad, por grupo y tipo de dato.

Reutiliza lo existente (descarga de grupos, `run_import`, procesamiento de actas e `ingest_tables`); añade la barrera de calidad,
el registro de ejecución y la frescura. Unidades atómicas: la importación de un grupo (una transacción), cada acta (una transacción)
y cada captura de tabla (una transacción). Un fallo de una unidad no deshace las anteriores ni impide el resto, pero deja la
ejecución en `partial`/`failed` y el tipo de dato sin avanzar su «última actualización válida».
"""

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from futsal.db.models import (
    Competition,
    CompetitionGroup,
    Match,
    MatchPlayer,
    MatchReport,
    MatchReportObservation,
    Round,
)
from futsal.importer.schema import InputError, read_league
from futsal.importer.service import run_import
from futsal.importer.table_service import ingest_tables
from futsal.importer.transform import build_plan
from futsal.ingestion.rffm.catalog_ingest import DOWNLOADED, PARTIAL, group_dir, iter_groups, scrape_group
from futsal.ingestion.rffm.client import FetchError
from futsal.ingestion.rffm.league import Target
from futsal.ingestion.rffm.parser import RffmParseError
from futsal.ingestion.rffm.update_policy import UpdatePolicy
from futsal.refresh.freshness import DATASETS, record_dataset
from futsal.refresh.gates import (
    FAILED,
    NO_CHANGES,
    NO_DATA,
    PARTIAL as PARTIAL_STATUS,
    REJECTED,
    SKIPPED,
    SUCCESS,
    GateResult,
    assess_matches,
    assess_report,
)
from futsal.refresh.runs import finish_run, start_run
from futsal.repositories.league import RegressionPolicy
from futsal.repositories.match_reports import Candidate, select_candidates

Fetch = Callable[[str], str]
MODES = ("incremental", "full")
UNPUBLISHED = ("HTTP 404", "HTTP 410")  # error de descarga que significa «acta aún no publicada»


@dataclass(frozen=True)
class RefreshOptions:
    datasets: tuple[str, ...] = DATASETS
    mode: str = "incremental"
    dry_run: bool = False
    offline: bool = False
    update_policy: UpdatePolicy = field(default_factory=UpdatePolicy)
    regression: RegressionPolicy = field(default_factory=RegressionPolicy)
    table_max_age_hours: float = 12.0
    report_limit: int | None = None


class UnknownScope(ValueError):
    """Se pidió una competición o grupo que no está en el inventario."""


def filter_inventory(inventory: dict[str, Any], competitions: set[str] | None, groups: set[str] | None) -> dict[str, Any]:
    """Inventario reducido al alcance pedido. Un ID que no existe es un error (no se ignora en silencio)."""
    known_c = {e["competition"]["external_id"] for e in inventory["entries"] if e.get("competition")}
    known_g = {g["external_id"] for e in inventory["entries"] if e.get("competition") for g in e.get("groups", [])}
    unknown = sorted((competitions or set()) - known_c) + sorted((groups or set()) - known_g)
    if unknown:
        raise UnknownScope("no están en el inventario: " + ", ".join(unknown))
    entries = []
    for e in inventory["entries"]:
        comp = e.get("competition")
        if comp is None or (competitions and comp["external_id"] not in competitions):
            continue
        kept = [g for g in e.get("groups", []) if not groups or g["external_id"] in groups]
        if kept:
            entries.append({**e, "groups": kept})
    return {**inventory, "entries": entries}


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


def _stored_counts(engine: Engine, comp_ext: str, group_ext: str) -> tuple[int, int, int | None]:
    with Session(engine) as s:
        gid = s.scalar(select(CompetitionGroup.id).join(Competition, Competition.id == CompetitionGroup.competition_id).where(
            CompetitionGroup.external_id == group_ext, Competition.external_id == comp_ext))
        if gid is None:
            return 0, 0, None
        matches = int(s.scalar(select(func.count()).select_from(Match).where(Match.group_id == gid)) or 0)
        rounds = int(s.scalar(select(func.count()).select_from(Round).where(Round.group_id == gid)) or 0)
        return matches, rounds, int(gid)


def _local_download_status(league_path: Path, expected_rounds: int | None) -> tuple[str, str | None]:
    """Sin red: completitud del snapshot local (todas las jornadas con estado ok y las esperadas)."""
    try:
        league = read_league(league_path)
    except InputError as exc:
        return "failed", str(exc)[:200]
    bad = [r.external_round_id for r in league.rounds if r.status != "ok"]
    if bad or (expected_rounds is not None and len(league.rounds) != expected_rounds):
        return PARTIAL, f"jornadas no ok: {bad}" if bad else f"{len(league.rounds)} jornadas de {expected_rounds} esperadas"
    return DOWNLOADED, None


def _finalize_matches(entry: dict[str, Any], gate: GateResult, rep: Any) -> None:
    if rep.status == "failed":
        entry.update(status=FAILED, error_kind="persistence", message=rep.error)
        return
    counts = {"inserted": rep.total("inserted"), "updated": rep.total("updated"), "unchanged": rep.total("unchanged"),
              "observations": rep.stats.get("observations", Counter()).get("inserted", 0),
              "held": rep.stats.get("matches", Counter()).get("held", 0),
              "skipped": rep.stats.get("matches", Counter()).get("skipped", 0)}
    entry["counts"] = counts
    entry["issue_codes"] = dict(rep.issue_codes)
    if rep.status == "partial" or gate.decision == "apply_partial":
        reasons = list(gate.reasons) + [f"{k}={v}" for k, v in sorted(rep.issue_codes.items())
                                         if k in ("identity_conflict", "finished_regression_held")]
        entry.update(status=PARTIAL_STATUS, message="; ".join(reasons))
    elif counts["inserted"] + counts["updated"] + counts["observations"] == 0:
        entry["status"] = NO_CHANGES
    else:
        entry["status"] = SUCCESS


def _refresh_matches(engine: Engine, comp: dict[str, Any], g: dict[str, Any], target: Target, work: Path, fetch: Fetch,
                     opts: RefreshOptions, requests_made: Callable[[], int], now: Callable[[], datetime],
                     run_id: int | None) -> dict[str, Any]:
    entry: dict[str, Any] = {"status": FAILED}
    gdir = group_dir(work, target.competition, target.group)
    league_path = gdir / "league.json"
    try:
        if opts.offline:
            if not league_path.exists():
                entry.update(status=NO_DATA, message="sin descarga local del grupo (modo --offline)")
                return entry
            dl_status, dl_error = _local_download_status(league_path, g.get("expected_rounds"))
        else:
            update = opts.update_policy if opts.mode == "incremental" else None
            res = scrape_group(target, gdir, fetch, g.get("expected_rounds"), refresh=opts.mode == "full", resume=True,
                               max_rounds=None, requests_made=requests_made, update=update, now=now)
            dl_status, dl_error = res["status"], res.get("error")
            entry["download"] = {k: res.get(k) for k in ("rounds_discovered", "rounds_downloaded", "rounds_from_cache",
                                                         "rounds_failed", "matches_total") if res.get(k) is not None}
        plan_counts = (0, 0)
        got_ids: tuple[str, str, str] | None = None
        if dl_status in (DOWNLOADED, PARTIAL) and league_path.exists():
            league = read_league(league_path)
            plan = build_plan(league)
            plan_counts = (len(plan.matches), len(plan.rounds))
            got_ids = (league.season.external_id, league.competition.external_id, league.group.external_id)
            entry["source_fetched_at"] = _utc(league_path.stat().st_mtime).isoformat()
        stored_matches, stored_rounds, _gid = _stored_counts(engine, target.competition, target.group)
        gate = assess_matches(download_status=dl_status, download_error=dl_error,
                              expected_ids=(target.season, target.competition, target.group), got_ids=got_ids,
                              plan_matches=plan_counts[0], plan_rounds=plan_counts[1], stored_matches=stored_matches,
                              stored_rounds=stored_rounds)
        entry["gate"] = {"decision": gate.decision, "reasons": gate.reasons}
        if gate.decision == "reject":
            entry.update(status=gate.status, message="; ".join(gate.reasons),
                         error_kind="network_or_source" if gate.status == FAILED else "validation")
            return entry
        rep = run_import(engine, league_path, dry_run=opts.dry_run, policy=opts.regression)
        entry["child_run_id"] = rep.run_id
        _finalize_matches(entry, gate, rep)
    except FetchError as exc:
        entry.update(status=FAILED, error_kind="network", message=str(exc)[:300])
    except (RffmParseError, InputError) as exc:
        entry.update(status=FAILED, error_kind="parse", message=str(exc)[:300])
    except SQLAlchemyError as exc:
        entry.update(status=FAILED, error_kind="persistence", message=f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
    return entry


def stored_report_shape(engine: Engine, match_external_id: str) -> tuple[int, bool] | None:
    """(jugadores, cerrada) de la observación vigente del acta del partido, o None si no hay acta."""
    with Session(engine) as s:
        obs = s.execute(select(MatchReportObservation.id, MatchReportObservation.closed).join(
            MatchReport, MatchReport.id == MatchReportObservation.report_id).where(
            MatchReport.external_id == match_external_id).order_by(
            MatchReportObservation.observed_at.desc(), MatchReportObservation.id.desc()).limit(1)).first()
        if obs is None:
            return None
        players = int(s.scalar(select(func.count()).select_from(MatchPlayer).where(MatchPlayer.observation_id == obs[0])) or 0)
        return players, bool(obs[1])


def _refresh_reports(engine: Engine, group_ext: str, work_reports: Path, fetch: Fetch, opts: RefreshOptions,
                     requests_made: Callable[[], int], now: Callable[[], datetime]) -> dict[str, Any]:
    from futsal.report_batch import process_reports  # import local: report_batch arrastra el CLI de actas

    entry: dict[str, Any] = {"status": FAILED}
    try:
        with Session(engine) as s:
            pending = select_candidates(s, [group_ext], opts.report_limit)
            recheck: list[Candidate] = select_candidates(s, [group_ext], opts.report_limit, with_report=True) \
                if opts.mode == "full" else []
        entry["pending"], entry["recheck"] = len(pending), len(recheck)
        if opts.dry_run or opts.offline:
            entry.update(status=SKIPPED, message="dry-run/offline: no se descargan actas" if pending or recheck else None)
            return entry
        if not pending and not recheck:
            entry["status"] = NO_CHANGES
            return entry

        def gate(report: Any) -> tuple[bool, list[str]]:
            stored = stored_report_shape(engine, report.report.match_external_id)
            result = assess_report(new_players=len(report.players), new_closed=bool(report.report.closed),
                                   stored_players=None if stored is None else stored[0],
                                   stored_closed=None if stored is None else stored[1])
            return result.decision == "apply", result.reasons

        outcomes: list[dict[str, Any]] = []
        for candidates, force in ((pending, False), (recheck, True)):
            if candidates:
                outcomes.append(process_reports(engine, work_reports, fetch, limit=None, requests_made=requests_made, now=now,
                                                groups=[group_ext], candidates_override=candidates, force=force, gate=gate,
                                                unpublished_errors=UNPUBLISHED))
        entries = [e for o in outcomes for e in o["matches"]]
        imported = sum(o["imported"] for o in outcomes)
        rejected = sum(o.get("rejected", 0) for o in outcomes)
        # HTTP 404/410 del acta = aún no publicada (ausencia temporal, no fallo); 403/429/5xx, red, timeout o página ilegible = fallo
        unavailable = [e for e in entries if e["status"] == "failed" and any(str(e.get("error", "")) == "FetchError: " + u for u in UNPUBLISHED)]
        failed = [e for e in entries if e["status"] == "failed" and e not in unavailable]
        new_records = sum(1 for e in entries if e["status"] == "imported" and e.get("records_inserted"))
        aborted = [o["aborted"] for o in outcomes if o["aborted"]]
        entry["counts"] = {"imported": imported, "with_new_records": new_records, "failed": len(failed), "rejected": rejected,
                           "unavailable": len(unavailable)}
        errors = [str(e["error"]) for e in failed + [e for e in entries if e["status"] == "rejected"]][:3]
        if failed or rejected or aborted:
            entry.update(status=FAILED if not imported else PARTIAL_STATUS, error_kind="source_or_validation",
                         message="; ".join(errors + aborted)[:500])
        else:
            entry["status"] = SUCCESS if new_records else NO_CHANGES
            if unavailable:
                entry["message"] = f"{len(unavailable)} acta(s) aún no publicadas (HTTP 404/410): se reintentarán"
    except (FetchError, RffmParseError) as exc:
        entry.update(status=FAILED, error_kind="network" if isinstance(exc, FetchError) else "parse", message=str(exc)[:300])
    except SQLAlchemyError as exc:
        entry.update(status=FAILED, error_kind="persistence", message=f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
    return entry


def _table_status(rep: dict[str, Any]) -> dict[str, Any]:
    """Resultado de `ingest_tables` para un grupo → estado de dataset."""
    g = rep["groups"][0]
    rounds = [r for r in g["rounds"] if r["outcome"] != "skipped"]
    entry: dict[str, Any] = {"rounds": [{k: r.get(k) for k in ("requested", "outcome", "change", "warnings", "error") if r.get(k) is not None}
                                        for r in g["rounds"]]}
    status = g.get("status")
    if status in ("no_group_in_db", "failed", "round_unavailable"):
        entry.update(status=FAILED, error_kind="source_or_persistence", message=g.get("error"))
    elif status == "incomplete":
        entry.update(status=PARTIAL_STATUS, message="; ".join(w for r in rounds for w in r.get("warnings", []))[:500])
    elif status == "skipped":
        entry["status"] = SKIPPED
    elif status == "not_applicable":
        entry.update(status=NO_DATA, message="grupo de eliminatorias: la RFFM no publica esta tabla")
    else:  # ok / empty
        entry["status"] = NO_CHANGES if all(r.get("change") == "unchanged" for r in rounds) else SUCCESS
        if status == "empty":
            entry["message"] = "tabla oficial vacía (válida)"
    return entry


def _refresh_table(engine: Engine, kind: str, inv: dict[str, Any], group_ext: str, tables_work: Path, fetch: Fetch,
                   opts: RefreshOptions, requests_made: Callable[[], int], now: Callable[[], datetime]) -> dict[str, Any]:
    try:
        rep = ingest_tables(engine, kind, inv, tables_work, fetch, groups={group_ext}, refresh=opts.mode == "full",
                            resume=opts.mode == "incremental", max_age_hours=opts.table_max_age_hours, offline=opts.offline,
                            dry_run=opts.dry_run, requests_made=requests_made, now=now)
        return _table_status(rep)
    except (FetchError, RffmParseError) as exc:
        return {"status": FAILED, "error_kind": "network" if isinstance(exc, FetchError) else "parse", "message": str(exc)[:300]}
    except SQLAlchemyError as exc:
        return {"status": FAILED, "error_kind": "persistence", "message": f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"}


def overall_status(outcomes: list[str]) -> str:
    """Estado de la ejecución a partir de los resultados por grupo y tipo de dato (nunca `success` si algo esencial falló)."""
    relevant = [o for o in outcomes if o not in (SKIPPED, NO_DATA)]
    if not relevant:
        return "no_changes"
    if all(o in (FAILED, REJECTED) for o in relevant):
        return "failed"
    if any(o in (FAILED, REJECTED, PARTIAL_STATUS) for o in relevant):
        return "partial"
    return "no_changes" if all(o == NO_CHANGES for o in relevant) else "success"


def run_refresh(engine: Engine, inventory: dict[str, Any], work: Path, tables_work: Path, reports_dir: Path, fetch: Fetch,
                opts: RefreshOptions, *, competitions: set[str] | None = None, groups: set[str] | None = None,
                requests_made: Callable[[], int] = lambda: 0, now: Callable[[], datetime] = lambda: datetime.now(UTC),
                on_group: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Ejecuta la actualización del alcance pedido y devuelve el informe (también guardado en `ingestion_runs.stats`)."""
    if opts.mode not in MODES:
        raise ValueError(f"modo desconocido '{opts.mode}' (válidos: {', '.join(MODES)})")
    bad = [d for d in opts.datasets if d not in DATASETS]
    if bad:
        raise ValueError(f"tipo de dato desconocido: {', '.join(bad)} (válidos: {', '.join(DATASETS)})")
    scoped = filter_inventory(inventory, competitions, groups)
    scope = {"competitions": sorted(competitions or []), "groups": sorted(groups or []), "datasets": list(opts.datasets),
             "mode": opts.mode, "dry_run": opts.dry_run, "offline": opts.offline}
    started = now()
    run_id = None if opts.dry_run else start_run(engine, "refresh", scope, started)
    report: dict[str, Any] = {"run_id": run_id, "scope": scope, "started_at": started.isoformat(), "groups": []}
    outcomes: list[str] = []
    totals: Counter[str] = Counter()
    before = requests_made()
    error: str | None = None
    try:
        for comp, g in iter_groups(scoped):
            target = Target(scoped["season"]["external_id"], comp["external_id"], g["external_id"], scoped["game_type"])
            single = filter_inventory(scoped, {comp["external_id"]}, {g["external_id"]})
            item: dict[str, Any] = {"competition_id": comp["external_id"], "group_id": g["external_id"], "group": g.get("name"),
                                    "datasets": {}}
            report["groups"].append(item)
            for dataset in opts.datasets:
                try:
                    if dataset == "matches":
                        out = _refresh_matches(engine, comp, g, target, work, fetch, opts, requests_made, now, run_id)
                    elif dataset == "reports":
                        gate_ok = item["datasets"].get("matches", {}).get("status") not in (FAILED, REJECTED)
                        out = (_refresh_reports(engine, g["external_id"], reports_dir, fetch, opts, requests_made, now) if gate_ok
                               else {"status": SKIPPED, "message": "los partidos del grupo no se actualizaron correctamente"})
                    else:
                        out = _refresh_table(engine, dataset, single, g["external_id"], tables_work, fetch, opts, requests_made, now)
                except Exception as exc:  # noqa: BLE001 - un tipo de dato que falla no impide el resto; queda registrado como fallo
                    out = {"status": FAILED, "error_kind": "unexpected", "message": f"{type(exc).__name__}: {exc}"[:300]}
                item["datasets"][dataset] = out
                outcomes.append(out["status"])
                totals[f"{dataset}:{out['status']}"] += 1
                for key, value in (out.get("counts") or {}).items():
                    if isinstance(value, int):
                        totals[f"{dataset}.{key}"] += value
                if not opts.dry_run and out["status"] != SKIPPED:
                    try:
                        _record(engine, comp["external_id"], g["external_id"], dataset, out, run_id, now())
                    except Exception as exc:  # noqa: BLE001 - sin trazabilidad no hay «correcto»: el tipo de dato queda parcial
                        out["message"] = f"{out.get('message') or ''} [frescura no registrada: {type(exc).__name__}]".strip()
                        if out["status"] in (SUCCESS, NO_CHANGES):
                            out["status"] = PARTIAL_STATUS
                        outcomes[-1] = out["status"]
            if on_group:
                on_group(item)
    except KeyboardInterrupt:
        report.update(status="cancelled", error="interrumpida por el usuario", finished_at=now().isoformat(),
                      http_requests=requests_made() - before)
        if run_id is not None:
            finish_run(engine, run_id, "cancelled", report, error="interrumpida por el usuario", now=now())
        raise
    except Exception as exc:  # noqa: BLE001 - un error inesperado deja la ejecución cerrada como fallida, nunca como correcta
        error = f"{type(exc).__name__}: {exc}"[:500]
    status = "failed" if error else overall_status(outcomes)
    if not report["groups"] and not error:
        report["warning"] = "ningún grupo del alcance pedido: nada que actualizar"
    report.update(status=status, error=error, totals=dict(sorted(totals.items())), http_requests=requests_made() - before,
                  finished_at=now().isoformat())
    if run_id is not None:
        counts = {"matches_processed": totals["matches.inserted"] + totals["matches.updated"] + totals["matches.unchanged"],
                  "records_inserted": totals["matches.inserted"], "records_updated": totals["matches.updated"],
                  "records_unchanged": totals["matches.unchanged"], "observations_created": totals["matches.observations"],
                  "issues_created": totals["matches.held"] + totals["matches.skipped"]}
        if not finish_run(engine, run_id, status, report, error=error, counts=counts, now=now()):
            report["warning"] = f"la ejecución {run_id} ya figuraba como abandonada y no se pudo cerrar con su estado final"
    return report


def _record(engine: Engine, comp_ext: str, group_ext: str, dataset: str, out: dict[str, Any], run_id: int | None,
            at: datetime) -> None:
    """Frescura de partidos y actas (las tablas se registran en sus propios intentos)."""
    if dataset not in ("matches", "reports"):
        return
    _m, _r, gid = _stored_counts(engine, comp_ext, group_ext)
    if gid is None:
        return
    fetched = datetime.fromisoformat(out["source_fetched_at"]) if out.get("source_fetched_at") else at
    with Session(engine) as s, s.begin():
        record_dataset(s, gid, dataset, out["status"], run_id=run_id, message=out.get("message"), source_fetched_at=fetched, now=at)


def dump_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True, default=str) + "\n", encoding="utf-8")
