"""Ingesta de clasificaciones y goleadores oficiales: descarga con caché (por grupo, aislada) + persistencia idempotente.

Cada grupo se procesa por separado: un fallo de un grupo, o de una de las dos tablas, no impide el resto. Todo intento queda
registrado en `table_ingest_attempts` (también los fallos), para poder reanudar y para informar de la cobertura.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from futsal.db.models import CompetitionGroup, Player, Team
from futsal.db.table_models import (
    ScorerRow,
    ScorerSnapshot,
    StandingsRow,
    StandingsSnapshot,
    TableIngestAttempt,
)
from futsal.ingestion.rffm.catalog_ingest import iter_groups
from futsal.ingestion.rffm.league import Target
from futsal.ingestion.rffm.progress import Progress
from futsal.ingestion.rffm.table_ingest import (
    Fetch,
    TableFetch,
    cache_path,
    fetch_table,
    standings_rounds,
)
from futsal.ingestion.rffm.tables import ScorerTable, StandingsTable
from futsal.repositories.table_queries import choose_snapshot

DONE = ("ok", "empty", "not_applicable")  # resultados que `--resume` no repite dentro de la ventana de edad
REPORT_NAMES = {"standings": "tables-standings-report.json", "scorers": "tables-scorers-report.json"}


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def standings_status(table: StandingsTable, expected_teams: int | None) -> tuple[str, list[str]]:
    """`ok`, `empty` o `incomplete` (filas descartadas, equipos repetidos o menos equipos de los que el grupo declara)."""
    warnings = list(table.warnings)
    if table.outcome == "empty":
        return "empty", warnings
    if expected_teams is not None and len(table.rows) != expected_teams:
        warnings.append(f"la tabla trae {len(table.rows)} equipos y el grupo declara {expected_teams}")
    return ("incomplete" if warnings else "ok"), warnings


def _scorers_status(table: ScorerTable) -> tuple[str, list[str]]:
    if table.outcome == "empty":
        return "empty", list(table.warnings)
    return ("incomplete" if table.warnings else "ok"), list(table.warnings)


def persist_standings(session: Session, group_id: int, fetched: TableFetch, expected_teams: int | None,
                      now: datetime) -> dict[str, Any]:
    table = fetched.table
    assert isinstance(table, StandingsTable)
    status, warnings = standings_status(table, expected_teams)
    number = table.round_number if table.round_number is not None else (table.current_round or 0)
    content = _hash({"round": number, "date": table.round_date, "rows": [r.model_dump() for r in table.rows]})
    snap = session.scalar(select(StandingsSnapshot).where(
        StandingsSnapshot.group_id == group_id, StandingsSnapshot.round_number == number,
        StandingsSnapshot.content_hash == content))
    fetched_at = fetched.fetched_at or now
    if snap is not None:
        snap.last_seen_at, snap.fetched_at, snap.current_round = now, fetched_at, table.current_round
        change = "unchanged"
    else:
        snap = StandingsSnapshot(
            group_id=group_id, round_number=number, round_date=table.round_date, current_round=table.current_round,
            status=status, content_hash=content, row_count=len(table.rows), expected_teams=expected_teams,
            source_url=fetched.url, fetched_at=fetched_at, first_seen_at=now, last_seen_at=now,
            meta={"promotions": table.promotions, "warnings": warnings})
        session.add(snap)
        session.flush()
        teams = {t.external_id: t.id for t in session.scalars(select(Team).where(Team.group_id == group_id))}
        session.add_all(StandingsRow(
            snapshot_id=snap.id, position=r.position, team_external_id=r.team_external_id, team_id=teams.get(r.team_external_id),
            team_name=r.team_name, played=r.played, won=r.won, drawn=r.drawn, lost=r.lost, goals_for=r.goals_for,
            goals_against=r.goals_against, points=r.points, penalty_points=r.penalty_points, details=r.details)
            for r in table.rows)
        change = "new"
    return {"outcome": status, "change": change, "snapshot_id": snap.id, "returned_round": number, "warnings": warnings}


def persist_scorers(session: Session, group_id: int, fetched: TableFetch, now: datetime) -> dict[str, Any]:
    table = fetched.table
    assert isinstance(table, ScorerTable)
    status, warnings = _scorers_status(table)
    content = _hash([r.model_dump() for r in table.rows])
    snap = session.scalar(select(ScorerSnapshot).where(ScorerSnapshot.group_id == group_id, ScorerSnapshot.content_hash == content))
    fetched_at = fetched.fetched_at or now
    if snap is not None:
        snap.last_seen_at, snap.fetched_at = now, fetched_at
        return {"outcome": status, "change": "unchanged", "snapshot_id": snap.id, "warnings": warnings}
    codes = [r.player_external_id for r in table.rows]
    dup = sorted({c for c in codes if codes.count(c) > 1})
    if dup:
        warnings.append(f"jugadores repetidos en la tabla (se conservan todas las filas): {', '.join(dup[:5])}")
    snap = ScorerSnapshot(
        group_id=group_id, status=status, content_hash=content, row_count=len(table.rows),
        goals_total=sum(r.goals for r in table.rows), source_url=fetched.url, fetched_at=fetched_at, first_seen_at=now,
        last_seen_at=now, meta={"warnings": warnings})
    session.add(snap)
    session.flush()
    teams = {t.external_id: t.id for t in session.scalars(select(Team).where(Team.group_id == group_id))}
    players = {p.external_id: p.id for p in session.scalars(select(Player).where(Player.external_id.in_(set(codes))))}

    def per_match(value: str | None) -> Decimal | None:
        try:
            return Decimal(value) if value else None
        except InvalidOperation:
            return None

    session.add_all(ScorerRow(
        snapshot_id=snap.id, row_order=r.row_order, player_external_id=r.player_external_id,
        player_id=players.get(r.player_external_id), player_name=r.player_name, team_external_id=r.team_external_id,
        team_id=teams.get(r.team_external_id), team_name=r.team_name, matches_played=r.matches_played, goals=r.goals,
        penalty_goals=r.penalty_goals, goals_per_match=per_match(r.goals_per_match), details=r.details or None)
        for r in table.rows)
    return {"outcome": status, "change": "new", "snapshot_id": snap.id, "warnings": warnings}


def record_attempt(session: Session, group_id: int, kind: str, requested: int | None, outcome: str, *, change: str | None = None,
                   error_code: str | None = None, error: str | None = None, snapshot_id: int | None = None,
                   returned_round: int | None = None, http_requests: int = 0, warnings: list[str] | None = None,
                   now: datetime) -> None:
    session.add(TableIngestAttempt(
        group_id=group_id, kind=kind, requested_round=requested, outcome=outcome, change=change, error_code=error_code,
        error_message=(error or None) and error[:500], snapshot_id=snapshot_id, returned_round=returned_round,
        http_requests=http_requests, warnings=warnings or None, attempted_at=now))


def _done_recently(session: Session, group_id: int, kind: str, requested: int | None, max_age_hours: float, now: datetime) -> bool:
    last = session.scalar(select(TableIngestAttempt).where(
        TableIngestAttempt.group_id == group_id, TableIngestAttempt.kind == kind,
        TableIngestAttempt.requested_round.is_(requested) if requested is None else TableIngestAttempt.requested_round == requested)
        .order_by(TableIngestAttempt.attempted_at.desc(), TableIngestAttempt.id.desc()).limit(1))
    return last is not None and last.outcome in DONE and (now - last.attempted_at) < timedelta(hours=max_age_hours)


def _group_status(rounds: list[dict[str, Any]]) -> str:
    states = {r["outcome"] for r in rounds}
    for bad in ("failed", "round_unavailable", "incomplete"):
        if bad in states:
            return bad
    return next(iter(states)) if len(states) == 1 else "ok"


def ingest_tables(engine: Engine | None, kind: str, inventory: dict[str, Any], work: Path, fetch: Fetch, *,
                  only: set[str] | None = None, groups: set[str] | None = None, limit_groups: int | None = None,
                  round_number: int | None = None, all_rounds: bool = False, refresh: bool = False, resume: bool = False,
                  max_age_hours: float = 12.0, null_retries: int = 2, offline: bool = False, dry_run: bool = False,
                  requests_made: Callable[[], int] = lambda: 0, progress: Progress | None = None,
                  sleep: Callable[[float], None] | None = None,
                  now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> dict[str, Any]:
    """Ingiere `kind` (standings|scorers) de cada grupo del inventario; el informe se guarda tras cada grupo."""
    assert kind in REPORT_NAMES
    pairs = [(c, g) for c, g in iter_groups(inventory, only) if not groups or g["external_id"] in groups]
    pairs = pairs[:limit_groups] if limit_groups else pairs
    report: dict[str, Any] = {"kind": kind, "season": inventory["season"], "started_at": now().isoformat(), "groups": []}
    db_groups: dict[str, int] = {}
    if engine is not None and not dry_run:
        with Session(engine) as s:
            db_groups = {e: i for e, i in s.execute(select(CompetitionGroup.external_id, CompetitionGroup.id))}
    kwargs: dict[str, Any] = {} if sleep is None else {"sleep": sleep}
    for index, (comp, g) in enumerate(pairs, start=1):
        target = Target(inventory["season"]["external_id"], comp["external_id"], g["external_id"], inventory["game_type"])
        if progress:
            progress.start_group(index, comp["external_id"], g["external_id"], g.get("name"))
        res: dict[str, Any] = {"competition_id": comp["external_id"], "competition_name": comp["name"], "group_id": g["external_id"],
                               "group_name": g["name"], "kind": kind, "expected_teams": g.get("expected_teams"), "rounds": []}
        before = requests_made()
        db_id = db_groups.get(g["external_id"])
        if engine is not None and not dry_run and db_id is None:
            res.update(status="no_group_in_db", error="el grupo no está importado en PostgreSQL (import-catalog)")
        else:
            _run_group(engine, db_id, kind, target, res, work, fetch, round_number=round_number, all_rounds=all_rounds,
                       refresh=refresh, resume=resume, max_age_hours=max_age_hours, null_retries=null_retries, offline=offline,
                       dry_run=dry_run, now=now, progress=progress, kwargs=kwargs)
        res["http_requests"] = requests_made() - before
        report["groups"].append(res)
        if progress:
            progress.end_group(res)
        report["finished_at"] = now().isoformat()
        _write(work, kind, report)
    report["finished_at"] = now().isoformat()
    _write(work, kind, report)
    return report


def _run_group(engine: Engine | None, db_id: int | None, kind: str, target: Target, res: dict[str, Any], work: Path, fetch: Fetch,
               *, round_number: int | None, all_rounds: bool, refresh: bool, resume: bool, max_age_hours: float,
               null_retries: int, offline: bool, dry_run: bool, now: Callable[[], datetime], progress: Progress | None,
               kwargs: dict[str, Any]) -> None:
    rounds: list[int | None] = [round_number]  # None: la jornada actual (sin parámetro)
    done = 0
    i = 0
    while i < len(rounds):
        requested = rounds[i]
        i += 1
        if resume and engine is not None and db_id is not None and not dry_run and not refresh:
            with Session(engine) as s:
                skip = _done_recently(s, db_id, kind, requested, max_age_hours, now())
            if skip:
                res["rounds"].append({"requested": requested, "outcome": "skipped", "note": "ya ingerida dentro de la ventana --max-age-hours"})
                continue
        history = kind == "standings" and requested is not None
        got = fetch_table(kind, target, fetch, cache_path(work, target.competition, target.group, kind, requested),
                          round_number=requested, refresh=refresh, max_age_hours=None if history else max_age_hours,
                          null_retries=null_retries, now=now, cache_only=offline, **kwargs)
        entry = _store(engine, db_id, kind, got, res.get("expected_teams"), requested, dry_run, now)
        res["rounds"].append(entry)
        done += 1
        if kind == "standings" and all_rounds and round_number is None and i == 1 and got.ok \
                and isinstance(got.table, StandingsTable):  # historial: de la 1 a la anterior a la actual
            rounds += [r for r in standings_rounds(got.table.current_round, round_number=None, all_rounds=True) if r is not None]
        if progress:
            progress.on_round(done, len(rounds), got.from_cache, bool(got.error_code))
    res["status"] = _group_status([r for r in res["rounds"] if r["outcome"] != "skipped"]) if any(
        r["outcome"] != "skipped" for r in res["rounds"]) else "skipped"
    last = next((r for r in reversed(res["rounds"]) if r.get("error")), None)
    res["error"] = last["error"] if last else None


def _store(engine: Engine | None, db_id: int | None, kind: str, got: TableFetch, expected_teams: int | None, requested: int | None,
           dry_run: bool, now: Callable[[], datetime]) -> dict[str, Any]:
    entry: dict[str, Any] = {"requested": requested, "from_cache": got.from_cache, "url": got.url}
    table = got.table
    if got.error_code or table is None:
        entry.update(outcome="round_unavailable" if got.error_code == "round_unavailable" else "failed",
                     error_code=got.error_code, error=got.error)
        persisted: dict[str, Any] = {}
    elif isinstance(table, StandingsTable) and table.outcome == "not_applicable":
        entry.update(outcome="not_applicable", note="grupo de eliminatorias: la RFFM no publica clasificación")
        persisted = {}
    else:
        if isinstance(table, StandingsTable):
            status, warnings = standings_status(table, expected_teams)
            entry.update(outcome=status, returned_round=table.round_number, current_round=table.current_round,
                         rows=len(table.rows), warnings=warnings)
        else:
            assert isinstance(table, ScorerTable)
            status, warnings = _scorers_status(table)
            entry.update(outcome=status, rows=len(table.rows), goals_total=sum(r.goals for r in table.rows), warnings=warnings)
        persisted = {}
    if engine is None or dry_run or db_id is None:
        return entry
    try:
        with Session(engine) as s, s.begin():
            if entry["outcome"] in ("ok", "empty", "incomplete") and table is not None:
                persisted = (persist_standings(s, db_id, got, expected_teams, now()) if isinstance(table, StandingsTable)
                             else persist_scorers(s, db_id, got, now()))
                entry.update(change=persisted["change"], snapshot_id=persisted["snapshot_id"], warnings=persisted["warnings"])
            record_attempt(s, db_id, kind, requested, entry["outcome"], change=persisted.get("change"),
                           error_code=entry.get("error_code"), error=entry.get("error"), snapshot_id=persisted.get("snapshot_id"),
                           returned_round=entry.get("returned_round"), http_requests=0 if got.from_cache else 1,
                           warnings=entry.get("warnings"), now=now())
    except SQLAlchemyError as exc:
        entry.update(outcome="failed", error_code="database", error=f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
    return entry


def _write(work: Path, kind: str, report: dict[str, Any]) -> None:
    work.mkdir(parents=True, exist_ok=True)
    (work / REPORT_NAMES[kind]).write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def coverage(engine: Engine, inventory: dict[str, Any], only: set[str] | None = None) -> dict[str, Any]:
    """Cobertura de lectura sobre PostgreSQL: por grupo del inventario, estado de ambas tablas y último intento."""
    out: list[dict[str, Any]] = []
    with Session(engine) as s:
        ids = {e: i for e, i in s.execute(select(CompetitionGroup.external_id, CompetitionGroup.id))}
        for comp, g in iter_groups(inventory, only):
            gid = ids.get(g["external_id"])
            row: dict[str, Any] = {"competition_id": comp["external_id"], "competition": comp["name"], "group_id": g["external_id"],
                                   "group": g["name"], "expected_teams": g.get("expected_teams"), "in_db": gid is not None}
            for kind in ("standings", "scorers"):
                row[kind] = _kind_coverage(s, gid, kind) if gid is not None else {"status": "no_group_in_db"}
            out.append(row)
    states: dict[str, dict[str, int]] = {"standings": {}, "scorers": {}}
    for r in out:
        for kind, counts in states.items():
            st = r[kind]["status"]
            counts[st] = counts.get(st, 0) + 1
    return {"groups": len(out), "competitions": len({r["competition_id"] for r in out}), "by_status": states, "detail": out}


def _kind_coverage(s: Session, gid: int, kind: str) -> dict[str, Any]:
    last = s.scalar(select(TableIngestAttempt).where(TableIngestAttempt.group_id == gid, TableIngestAttempt.kind == kind)
                    .order_by(TableIngestAttempt.attempted_at.desc(), TableIngestAttempt.id.desc()).limit(1))
    standings_snap = scorers_snap = None
    rounds: list[int] = []
    if kind == "standings":
        standings_snap, _ = choose_snapshot(s, StandingsSnapshot, [StandingsSnapshot.group_id == gid], [
            StandingsSnapshot.round_number.desc(), StandingsSnapshot.last_seen_at.desc(), StandingsSnapshot.id.desc()])
        rounds = sorted(r for (r,) in s.execute(select(StandingsSnapshot.round_number).where(StandingsSnapshot.group_id == gid).distinct()))
    else:
        scorers_snap, _ = choose_snapshot(s, ScorerSnapshot, [ScorerSnapshot.group_id == gid],
                                          [ScorerSnapshot.last_seen_at.desc(), ScorerSnapshot.id.desc()])
    snap = standings_snap or scorers_snap
    info: dict[str, Any] = {"last_attempt": None if last is None else {
        "outcome": last.outcome, "at": last.attempted_at.isoformat(), "error_code": last.error_code, "error": last.error_message}}
    if snap is None:
        info["status"] = ("not_applicable" if last is not None and last.outcome == "not_applicable"
                          else "failed" if last is not None and last.outcome in ("failed", "round_unavailable") else "not_ingested")
        return info
    info.update(status=snap.status, rows=snap.row_count, fetched_at=snap.fetched_at.isoformat(), last_seen_at=snap.last_seen_at.isoformat())
    if standings_snap is not None:
        info.update(round=standings_snap.round_number, current_round=standings_snap.current_round, rounds_available=rounds,
                    teams_expected=standings_snap.expected_teams)
    elif scorers_snap is not None:
        info["goals_total"] = scorers_snap.goals_total
        served_standings, _ = choose_snapshot(s, StandingsSnapshot, [StandingsSnapshot.group_id == gid], [
            StandingsSnapshot.round_number.desc(), StandingsSnapshot.last_seen_at.desc(), StandingsSnapshot.id.desc()])
        team_goals = None if served_standings is None else s.scalar(
            select(func.sum(StandingsRow.goals_for)).where(StandingsRow.snapshot_id == served_standings.id))
        info["team_goals_in_standings"] = int(team_goals) if team_goals is not None else None
    if last is not None and last.outcome in ("failed", "round_unavailable"):
        info["stale"] = "el último intento falló: se muestra la última captura buena"
    return info
