"""Repositorios de importación: upsert idempotente por identificador externo."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypeVar, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from futsal.db.models import (
    Competition,
    DataQualityIssue,
    CompetitionGroup,
    Match,
    MatchObservation,
    MatchReport,
    MatchReportObservation,
    Round,
    Season,
    Team,
    Tracked,
)
from futsal.importer.transform import SOURCE, ImportPlan, Issue, MatchRec, content_hash

T = TypeVar("T", bound=Tracked)

# entidad -> contadores inserted/updated/unchanged
Stats = dict[str, Counter[str]]


def _upsert(session: Session, model: type[T], keys: Mapping[str, Any], values: Mapping[str, Any],
            now: datetime, stats: Stats, label: str) -> T:
    row = session.scalars(select(model).filter_by(source=SOURCE, **keys)).one_or_none()
    if row is None:
        row = cast(T, cast(Any, model)(source=SOURCE, first_seen_at=now, last_seen_at=now, **keys, **values))
        session.add(row)
        session.flush()
        stats[label]["inserted"] += 1
        return row
    changed = {k: v for k, v in values.items() if getattr(row, k) != v}
    row.last_seen_at = now
    if changed:
        for k, v in changed.items():
            setattr(row, k, v)
        stats[label]["updated"] += 1
    else:
        stats[label]["unchanged"] += 1
    session.flush()
    return row


@dataclass(frozen=True)
class RegressionPolicy:
    """Cuándo una regresión de un partido `finished` deja de ser sospechosa (ver `docs/phase21-data-refresh-consistency.md`).

    El listado de la RFFM solo ofrece estado y marcador, sin fecha de publicación ni indicador de corrección. La única evidencia
    verificable es la persistencia: el mismo contenido regresado devuelto en una ejecución anterior (retenida) y en esta, con al menos
    `confirm_after_hours` de diferencia, y sin acta cerrada que acredite que se jugó.
    """

    confirm_after_hours: float = 24.0


def _is_regression(stored: Match, m: MatchRec) -> bool:
    """`finished` almacenado frente a un listado que lo devuelve como no jugado o sin marcador."""
    if stored.status != "finished":
        return False
    return m.status in ("scheduled", "unknown") or (m.status == "finished" and m.home_score is None)


def _match_values(status: str, d: object, t: object, hs: int | None, aws: int | None) -> dict[str, Any]:
    return {"status": status, "date": str(d) if d else None, "time": str(t) if t else None, "home_score": hs, "away_score": aws}


def _regression_confirmed(session: Session, m: MatchRec, run_id: int, now: datetime, policy: RegressionPolicy) -> bool:
    cutoff = now - timedelta(hours=policy.confirm_after_hours)
    prior = session.scalars(select(DataQualityIssue).where(
        DataQualityIssue.code == "finished_regression_held", DataQualityIssue.entity_type == "match",
        DataQualityIssue.external_id == m.external_id, DataQualityIssue.ingestion_run_id != run_id)).all()
    for issue in prior:
        d = issue.details or {}
        if d.get("incoming_hash") == m.content_hash and datetime.fromisoformat(d["observed_at"]) <= cutoff:
            return True
    return False


REGRESSION_CODES = ("finished_regression_held", "finished_regression_confirmed", "finished_regression_resolved")


def latest_regression_issues(session: Session, external_ids: list[str]) -> dict[str, str]:
    """Código de la última incidencia de regresión de cada partido (los que no tienen ninguna no aparecen)."""
    rows = session.execute(
        select(DataQualityIssue.external_id, DataQualityIssue.code)
        .where(DataQualityIssue.entity_type == "match", DataQualityIssue.code.in_(REGRESSION_CODES),
               DataQualityIssue.external_id.in_(external_ids))
        .ext(distinct_on(DataQualityIssue.external_id)).order_by(DataQualityIssue.external_id, DataQualityIssue.id.desc())).all()
    return {str(e): str(c) for e, c in rows if e}


def _guard_match(session: Session, plan: ImportPlan, group: CompetitionGroup, m: MatchRec, run_id: int, now: datetime,
                 policy: RegressionPolicy, stats: Stats, outstanding: set[str]) -> bool:
    """False si el partido NO debe aplicarse (conflicto de identidad o regresión sospechosa); registra la incidencia."""
    stored = session.scalars(select(Match).filter_by(source=SOURCE, external_id=m.external_id)).one_or_none()
    if stored is None:
        return True
    if stored.group_id != group.id:
        other = session.get(CompetitionGroup, stored.group_id)
        other_comp = session.get(Competition, other.competition_id) if other else None
        plan.issues.append(Issue(
            "identity_conflict", "match", m.external_id,
            f"el partido {m.external_id} ya existe en el grupo {other.external_id if other else stored.group_id} "
            f"y la fuente lo trae en el grupo {plan.group_external_id}: no se modifica",
            {"match_external_id": m.external_id, "stored_match_id": stored.id,
             "stored_group_id": stored.group_id, "stored_group_external_id": other.external_id if other else None,
             "stored_competition_external_id": other_comp.external_id if other_comp else None,
             "incoming_group_external_id": plan.group_external_id,
             "incoming_competition_external_id": plan.competition_external_id}))
        stats["matches"]["skipped"] += 1
        return False
    if not _is_regression(stored, m):
        if m.external_id in outstanding:  # antes retenido y la fuente ya no regresa: queda cerrado
            plan.issues.append(Issue("finished_regression_resolved", "match", m.external_id,
                                     "la fuente ya no regresa el partido: se cierra la revisión pendiente",
                                     {"incoming_hash": m.content_hash}))
        return True
    closed_acta = _closed_acta_score(session, m.external_id) is not None
    if not closed_acta and _regression_confirmed(session, m, run_id, now, policy):
        plan.issues.append(Issue(
            "finished_regression_confirmed", "match", m.external_id,
            "la fuente mantiene el partido sin resultado en ejecuciones separadas: se aplica (el anterior queda en match_observations)",
            {"stored": _match_values(stored.status, stored.scheduled_date, stored.scheduled_time, stored.home_score, stored.away_score),
             "incoming": _match_values(m.status, m.scheduled_date, m.scheduled_time, m.home_score, m.away_score),
             "incoming_hash": m.content_hash}))
        return True
    stored.last_seen_at = now
    plan.issues.append(Issue(
        "finished_regression_held", "match", m.external_id,
        "la fuente devuelve como no jugado (o sin marcador) un partido finalizado: se conserva lo almacenado y queda pendiente de revisión",
        {"stored": _match_values(stored.status, stored.scheduled_date, stored.scheduled_time, stored.home_score, stored.away_score),
         "incoming": _match_values(m.status, m.scheduled_date, m.scheduled_time, m.home_score, m.away_score),
         "incoming_hash": m.content_hash, "observed_at": now.isoformat(), "closed_acta": closed_acta,
         "confirm_after_hours": policy.confirm_after_hours}))
    stats["matches"]["held"] += 1
    return False


def import_plan(session: Session, plan: ImportPlan, run_id: int, now: datetime,
                policy: RegressionPolicy = RegressionPolicy()) -> Stats:
    """Aplica el plan dentro de la transacción de `session`; devuelve contadores por entidad.

    Un partido con otro `group_id` (`identity_conflict`) o una regresión sospechosa de `finished` (`finished_regression_held`) no se
    modifican; ambos añaden una incidencia a `plan.issues`.
    """
    stats: Stats = {k: Counter() for k in
                    ("seasons", "competitions", "groups", "rounds", "teams", "matches",
                     "observations")}
    season = _upsert(session, Season, {"external_id": plan.season_external_id},
                     {"name": plan.season_name}, now, stats, "seasons")
    comp = _upsert(session, Competition, {"external_id": plan.competition_external_id},
                   {"season_id": season.id, "name": plan.competition_name}, now, stats,
                   "competitions")
    group = _upsert(session, CompetitionGroup, {"external_id": plan.group_external_id},
                    {"competition_id": comp.id, "name": plan.group_name}, now, stats, "groups")
    rounds = {
        r.external_id: _upsert(
            session, Round, {"group_id": group.id, "external_id": r.external_id},
            {"visible_label": r.visible_label, "visible_number": r.visible_number,
             "scheduled_date": r.scheduled_date, "source_url": r.source_url},
            now, stats, "rounds").id
        for r in plan.rounds
    }
    teams = {
        t.external_id: _upsert(
            session, Team, {"group_id": group.id, "external_id": t.external_id},
            {"name": t.name}, now, stats, "teams").id
        for t in plan.teams
    }
    outstanding = {e for e, c in latest_regression_issues(session, [x.external_id for x in plan.matches]).items()
                   if c == "finished_regression_held"}
    for m in plan.matches:
        if not _guard_match(session, plan, group, m, run_id, now, policy, stats, outstanding):
            continue
        acta = _closed_acta_score(session, m.external_id) if m.status == "finished" else None
        home_score, away_score = acta if acta else (m.home_score, m.away_score)
        row = _upsert(
            session, Match, {"external_id": m.external_id},
            {"group_id": group.id, "round_id": rounds[m.round_external_id],
             "home_team_id": teams[m.home_external_id], "away_team_id": teams[m.away_external_id],
             "scheduled_date": m.scheduled_date, "scheduled_time": m.scheduled_time,
             "scheduled_at": m.scheduled_at, "timezone": m.timezone, "venue": m.venue,
             "status": m.status, "home_score": home_score, "away_score": away_score,
             "source_url": m.source_url},
            now, stats, "matches")
        digest = (content_hash(m.status, m.scheduled_date, m.scheduled_time, home_score, away_score)
                  if acta else m.content_hash)
        _observe(session, row.id, m, run_id, now, stats, home_score, away_score, digest)
    return stats


def _closed_acta_score(session: Session, match_external_id: str) -> tuple[int, int] | None:
    """Marcador de la última observación de un acta cerrada: prevalece sobre el listado."""
    row = session.execute(
        select(MatchReportObservation.home_score, MatchReportObservation.away_score,
               MatchReportObservation.closed)
        .join(MatchReport, MatchReport.id == MatchReportObservation.report_id)
        .where(MatchReport.source == SOURCE, MatchReport.external_id == match_external_id)
        .order_by(MatchReportObservation.observed_at.desc(), MatchReportObservation.id.desc())
        .limit(1)).first()
    if row is None or not row.closed or row.home_score is None or row.away_score is None:
        return None
    return int(row.home_score), int(row.away_score)


def _observe(session: Session, match_id: int, m: MatchRec, run_id: int, now: datetime,
             stats: Stats, home_score: int | None, away_score: int | None, digest: str) -> None:
    last = session.scalars(
        select(MatchObservation.content_hash).where(MatchObservation.match_id == match_id)
        .order_by(MatchObservation.observed_at.desc(), MatchObservation.id.desc()).limit(1)
    ).one_or_none()
    if last == digest:
        return
    session.add(MatchObservation(
        match_id=match_id, ingestion_run_id=run_id, observed_at=now, status=m.status,
        home_score=home_score, away_score=away_score, scheduled_date=m.scheduled_date,
        scheduled_time=m.scheduled_time, scheduled_at=m.scheduled_at,
        content_hash=digest))
    stats["observations"]["inserted"] += 1
