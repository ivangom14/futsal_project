"""Repositorios de importación: upsert idempotente por identificador externo."""

from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from typing import Any, TypeVar, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from futsal.db.models import (
    Competition,
    CompetitionGroup,
    Match,
    MatchObservation,
    Round,
    Season,
    Team,
    Tracked,
)
from futsal.importer.transform import SOURCE, ImportPlan, MatchRec

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


def import_plan(session: Session, plan: ImportPlan, run_id: int, now: datetime) -> Stats:
    """Aplica el plan dentro de la transacción de `session`; devuelve contadores por entidad."""
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
    for m in plan.matches:
        row = _upsert(
            session, Match, {"external_id": m.external_id},
            {"group_id": group.id, "round_id": rounds[m.round_external_id],
             "home_team_id": teams[m.home_external_id], "away_team_id": teams[m.away_external_id],
             "scheduled_date": m.scheduled_date, "scheduled_time": m.scheduled_time,
             "scheduled_at": m.scheduled_at, "timezone": m.timezone, "venue": m.venue,
             "status": m.status, "home_score": m.home_score, "away_score": m.away_score,
             "source_url": m.source_url},
            now, stats, "matches")
        _observe(session, row.id, m, run_id, now, stats)
    return stats


def _observe(session: Session, match_id: int, m: MatchRec, run_id: int, now: datetime,
             stats: Stats) -> None:
    last = session.scalars(
        select(MatchObservation.content_hash).where(MatchObservation.match_id == match_id)
        .order_by(MatchObservation.observed_at.desc(), MatchObservation.id.desc()).limit(1)
    ).one_or_none()
    if last == m.content_hash:
        return
    session.add(MatchObservation(
        match_id=match_id, ingestion_run_id=run_id, observed_at=now, status=m.status,
        home_score=m.home_score, away_score=m.away_score, scheduled_date=m.scheduled_date,
        scheduled_time=m.scheduled_time, scheduled_at=m.scheduled_at,
        content_hash=m.content_hash))
    stats["observations"]["inserted"] += 1
