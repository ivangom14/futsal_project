"""Frescura por tipo de dato (partidos, actas, clasificación, goleadores). SOLO LECTURA salvo `record_dataset`.

No se confunden cuatro fechas: ejecución (`last_attempt.at`), consulta a la fuente (`source_fetched_at`), última actualización VÁLIDA
(`last_valid_at`) y modificación del registro (`updated_at`, no expuesta). La RFFM no publica fecha de publicación: no se inventa.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.orm import Session

from futsal.db.models import (
    Competition,
    CompetitionGroup,
    DatasetRefresh,
    DataQualityIssue,
    IngestionRun,
    Match,
    MatchObservation,
    MatchReport,
    MatchReportObservation,
)
from futsal.db.table_models import ScorerSnapshot, StandingsSnapshot, TableIngestAttempt
from futsal.refresh.gates import VALID_STATUSES
from futsal.repositories.league import REGRESSION_CODES
from futsal.repositories.table_queries import choose_snapshot

NOTE = ("Fechas de captura del sistema: la RFFM no publica fecha de publicación. «Última actualización válida» es la última vez que "
        "la fuente devolvió datos completos y válidos; un intento parcial o fallido posterior no la adelanta.")
DATASETS = ("matches", "reports", "standings", "scorers")


@dataclass(frozen=True)
class StalePolicy:
    """Horas sin actualización válida a partir de las cuales un tipo de dato se avisa como desactualizado."""

    matches: float = 72.0
    reports: float = 168.0
    standings: float = 96.0
    scorers: float = 168.0

    def for_dataset(self, dataset: str) -> float:
        return float(getattr(self, dataset))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def record_dataset(session: Session, group_id: int, dataset: str, status: str, *, run_id: int | None, message: str | None,
                   source_fetched_at: datetime | None, now: datetime) -> None:
    """Upsert atómico (restricción única grupo+tipo). Un estado válido avanza `last_valid_*`; cualquier otro solo el intento."""
    values: dict[str, Any] = {"group_id": group_id, "dataset": dataset, "last_attempt_at": now, "last_attempt_status": status,
                              "last_attempt_run_id": run_id, "last_attempt_message": (message or None) and message[:500]}
    update = {k: v for k, v in values.items() if k not in ("group_id", "dataset")}
    if status in VALID_STATUSES:
        values.update(last_valid_at=now, last_valid_run_id=run_id, source_fetched_at=source_fetched_at)
        update.update(last_valid_at=now, last_valid_run_id=run_id, source_fetched_at=source_fetched_at)
    session.execute(insert(DatasetRefresh).values(**values).on_conflict_do_update(
        constraint="uq_dataset_refreshes_group_id_dataset", set_=update))


def _entry(valid: datetime | None, fetched: datetime | None, attempt: dict[str, Any] | None, now: datetime, limit: float,
           basis: str, detail: dict[str, Any]) -> dict[str, Any]:
    age = None if valid is None else round((now - valid).total_seconds() / 3600, 1)
    state = "never" if valid is None else ("stale" if age is not None and age > limit else "fresh")
    return {k: v for k, v in {"status": state, "last_valid_at": _iso(valid), "source_fetched_at": _iso(fetched),
                              "age_hours": age, "stale_after_hours": limit, "last_attempt": attempt, "basis": basis,
                              "detail": detail}.items() if v is not None}


def _refresh_row(db: Session, group_id: int, dataset: str) -> DatasetRefresh | None:
    return db.scalar(select(DatasetRefresh).where(DatasetRefresh.group_id == group_id, DatasetRefresh.dataset == dataset))


def _attempt_of(row: DatasetRefresh | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: v for k, v in {"at": _iso(row.last_attempt_at), "status": row.last_attempt_status, "run_id": row.last_attempt_run_id,
                              "message": row.last_attempt_message}.items() if v is not None}


def _held_for_review(db: Session, group_id: int) -> int:
    """Partidos finalizados cuya última incidencia de regresión es «retenida» (ni confirmada ni resuelta)."""
    latest = (select(DataQualityIssue.external_id, DataQualityIssue.code).where(
        DataQualityIssue.entity_type == "match", DataQualityIssue.code.in_(REGRESSION_CODES))
        .ext(distinct_on(DataQualityIssue.external_id)).order_by(DataQualityIssue.external_id, DataQualityIssue.id.desc()).subquery())
    stmt = (select(func.count()).select_from(Match).join(latest, latest.c.external_id == Match.external_id)
            .where(Match.group_id == group_id, Match.status == "finished", latest.c.code == "finished_regression_held"))
    return int(db.scalar(stmt) or 0)


def _matches(db: Session, gid: int, now: datetime, policy: StalePolicy) -> dict[str, Any]:
    row = _refresh_row(db, gid, "matches")
    total = int(db.scalar(select(func.count()).select_from(Match).where(Match.group_id == gid)) or 0)
    detail: dict[str, Any] = {"matches": total, "held_for_review": _held_for_review(db, gid)}
    if row is not None:
        return _entry(row.last_valid_at, row.source_fetched_at, _attempt_of(row), now, policy.matches, "refresh_log", detail)
    seen = db.scalar(select(func.max(Match.last_seen_at)).where(Match.group_id == gid))
    return _entry(seen, None, None, now, policy.matches, "records", detail)


def _reports(db: Session, gid: int, now: datetime, policy: StalePolicy) -> dict[str, Any]:
    finished = int(db.scalar(select(func.count()).select_from(Match).where(
        Match.group_id == gid, Match.status == "finished")) or 0)
    with_report = int(db.scalar(select(func.count()).select_from(Match).join(MatchReport, MatchReport.match_id == Match.id).where(
        Match.group_id == gid, Match.status == "finished")) or 0)
    detail = {"finished_matches": finished, "with_report": with_report, "pending": max(finished - with_report, 0)}
    row = _refresh_row(db, gid, "reports")
    if row is not None:
        return _entry(row.last_valid_at, row.source_fetched_at, _attempt_of(row), now, policy.reports, "refresh_log", detail)
    seen = db.scalar(select(func.max(MatchReport.last_seen_at)).join(Match, Match.id == MatchReport.match_id).where(
        Match.group_id == gid))
    return _entry(seen, None, None, now, policy.reports, "records", detail)


def _table(db: Session, gid: int, kind: str, now: datetime, policy: StalePolicy) -> dict[str, Any]:
    last = db.scalar(select(TableIngestAttempt).where(TableIngestAttempt.group_id == gid, TableIngestAttempt.kind == kind)
                     .order_by(TableIngestAttempt.attempted_at.desc(), TableIngestAttempt.id.desc()).limit(1))
    attempt = None if last is None else {k: v for k, v in {
        "at": _iso(last.attempted_at), "status": last.outcome, "message": last.error_message}.items() if v is not None}
    if kind == "standings":
        snap, latest = choose_snapshot(db, StandingsSnapshot, [StandingsSnapshot.group_id == gid], [
            StandingsSnapshot.round_number.desc(), StandingsSnapshot.last_seen_at.desc(), StandingsSnapshot.id.desc()])
        limit = policy.standings
    else:
        snap, latest = choose_snapshot(db, ScorerSnapshot, [ScorerSnapshot.group_id == gid],
                                       [ScorerSnapshot.last_seen_at.desc(), ScorerSnapshot.id.desc()])
        limit = policy.scorers
    if last is not None and last.outcome == "not_applicable" and snap is None:
        return {"status": "not_applicable", "last_attempt": attempt, "basis": "records",
                "detail": {"note": "grupo de eliminatorias: la RFFM no publica esta tabla"}}
    if snap is None:
        return _entry(None, None, attempt, now, limit, "records", {})
    valid = snap.last_seen_at if snap.status == "ok" else None
    detail: dict[str, Any] = {"snapshot_status": snap.status}
    if kind == "standings":
        detail.update(round=snap.round_number, current_round=snap.current_round)
    if latest is not None and latest.id != snap.id and latest.last_seen_at > snap.last_seen_at:
        detail["latest_capture_status"] = latest.status
    entry = _entry(valid, snap.fetched_at, attempt, now, limit, "records", detail)
    if snap.status != "ok":  # empty: ausencia oficial de datos; incomplete: lo mejor disponible, nunca «fresco»
        entry["status"] = "empty" if snap.status == "empty" else "partial"
        entry.setdefault("last_capture_at", _iso(snap.fetched_at))
    return entry


def group_freshness(db: Session, group_id: int, *, now: datetime | None = None,
                    policy: StalePolicy = StalePolicy()) -> dict[str, Any] | None:
    group = db.get(CompetitionGroup, group_id)
    if group is None:
        return None
    now = now or datetime.now(UTC)
    competition = db.get(Competition, group.competition_id)
    datasets = {"matches": _matches(db, group_id, now, policy), "reports": _reports(db, group_id, now, policy),
                "standings": _table(db, group_id, "standings", now, policy), "scorers": _table(db, group_id, "scorers", now, policy)}
    names = {"matches": "Partidos", "reports": "Actas", "standings": "Clasificación", "scorers": "Goleadores"}
    warnings: list[str] = []
    for key, d in datasets.items():
        last = d.get("last_attempt") or {}
        if d["status"] == "never":
            warnings.append(f"{names[key]}: sin datos válidos actualizados.")
        elif d["status"] == "stale":
            warnings.append(f"{names[key]}: última actualización válida hace {d['age_hours']:g} h (umbral {d['stale_after_hours']:g} h).")
        elif d["status"] == "partial":
            warnings.append(f"{names[key]}: solo hay una captura parcial; no es una actualización válida.")
        if last.get("status") in ("partial", "failed", "rejected", "incomplete", "round_unavailable") and d["status"] not in ("never",):
            warnings.append(f"{names[key]}: el último intento ({last.get('at')}) fue {last['status']}"
                            + (f": {last['message']}" if last.get("message") else "") + "; se conservan los últimos datos válidos.")
    held = datasets["matches"]["detail"].get("held_for_review", 0)
    if held:
        warnings.append(f"Partidos: {held} partido(s) finalizado(s) con un cambio sospechoso de la fuente retenido pendiente de revisión.")
    pending = datasets["reports"]["detail"].get("pending", 0)
    if pending:
        warnings.append(f"Actas: {pending} partido(s) finalizado(s) sin acta incorporada.")
    run = db.scalar(select(IngestionRun).where(IngestionRun.kind == "refresh").order_by(IngestionRun.id.desc()).limit(1))
    last_run = None if run is None else {k: v for k, v in {
        "run_id": run.id, "status": run.status, "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at),
        "error": run.error_summary}.items() if v is not None}
    return {"group_id": group.id, "group": group.name, "competition_id": group.competition_id,
            "competition": competition.name if competition else None, "as_of": now.isoformat(), "datasets": datasets,
            "last_run": last_run, "warnings": warnings, "note": NOTE}


def competition_freshness(db: Session, competition_id: int, *, now: datetime | None = None,
                          policy: StalePolicy = StalePolicy()) -> dict[str, Any] | None:
    competition = db.get(Competition, competition_id)
    if competition is None:
        return None
    now = now or datetime.now(UTC)
    groups = []
    for g in db.scalars(select(CompetitionGroup).where(CompetitionGroup.competition_id == competition_id).order_by(CompetitionGroup.id)):
        f = group_freshness(db, g.id, now=now, policy=policy) or {}
        groups.append({"group_id": g.id, "group": g.name,
                       "datasets": {k: {x: v.get(x) for x in ("status", "last_valid_at", "age_hours") if v.get(x) is not None}
                                    for k, v in f.get("datasets", {}).items()},
                       "warnings": f.get("warnings", [])})
    return {"competition_id": competition.id, "competition": competition.name, "as_of": now.isoformat(), "groups": groups, "note": NOTE}


def latest_observation_times(db: Session, gid: int) -> dict[str, datetime | None]:
    """Último resultado/acta observado en el grupo (para la reconciliación: tablas anteriores a un resultado más reciente)."""
    finished = db.scalar(select(func.max(MatchObservation.observed_at)).join(Match, Match.id == MatchObservation.match_id).where(
        Match.group_id == gid, MatchObservation.status == "finished"))
    report = db.scalar(select(func.max(MatchReportObservation.observed_at)).join(
        MatchReport, MatchReport.id == MatchReportObservation.report_id).join(Match, Match.id == MatchReport.match_id).where(
        Match.group_id == gid))
    return {"finished": finished, "report": report}
