"""Transformación pura: JSON validado -> registros listos para persistir + incidencias."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from futsal.importer.schema import LeagueFile

SOURCE = "rffm"
VALID_STATUS = {"scheduled", "finished", "postponed", "suspended", "cancelled", "unknown"}


@dataclass(frozen=True)
class Issue:
    code: str
    entity_type: str
    external_id: str | None
    message: str


@dataclass(frozen=True)
class RoundRec:
    external_id: str
    visible_label: str
    visible_number: int | None
    scheduled_date: date | None
    source_url: str


@dataclass(frozen=True)
class TeamRec:
    external_id: str
    name: str | None


@dataclass(frozen=True)
class MatchRec:
    external_id: str
    round_external_id: str
    home_external_id: str
    away_external_id: str
    scheduled_date: date | None
    scheduled_time: time | None
    scheduled_at: datetime | None
    timezone: str
    venue: str | None
    status: str
    home_score: int | None
    away_score: int | None
    source_url: str
    content_hash: str


@dataclass
class ImportPlan:
    season_external_id: str
    season_name: str | None
    competition_external_id: str
    competition_name: str | None
    group_external_id: str
    group_name: str | None
    rounds: list[RoundRec] = field(default_factory=list)
    teams: list[TeamRec] = field(default_factory=list)
    matches: list[MatchRec] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def content_hash(status: str, scheduled_date: date | None, scheduled_time: time | None,
                 home_score: int | None, away_score: int | None) -> str:
    """Hash estable del estado observable; sin timestamps de ejecución."""
    payload = {
        "status": status,
        "date": scheduled_date.isoformat() if scheduled_date else None,
        "time": scheduled_time.isoformat() if scheduled_time else None,
        "home_score": home_score,
        "away_score": away_score,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _scheduled_at(d: date | None, t: time | None, tz: str) -> datetime | None:
    if d is None or t is None:
        return None
    return datetime.combine(d, t, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def build_plan(league: LeagueFile) -> ImportPlan:
    plan = ImportPlan(
        league.season.external_id, league.season.name,
        league.competition.external_id, league.competition.name,
        league.group.external_id, league.group.name,
    )
    round_ids: set[str] = set()
    for r in league.rounds:
        if r.external_round_id in round_ids:
            plan.issues.append(Issue("duplicate_round", "round", r.external_round_id,
                                     "jornada repetida en el fichero"))
            continue
        round_ids.add(r.external_round_id)
        if r.status != "ok":
            plan.issues.append(Issue("round_not_ok", "round", r.external_round_id,
                                     f"estado de descarga '{r.status}'"))
        plan.rounds.append(RoundRec(r.external_round_id, r.visible_round_label,
                                    r.visible_round_number, r.scheduled_date, r.source_url))

    teams: dict[str, str | None] = {}
    seen: set[str] = set()
    per_round: dict[str, int] = {}
    for m in league.matches:
        mid = m.external_match_id
        problem: str | None = None
        code = "invalid_match"
        if not mid:
            problem, code = "partido sin identificador externo", "missing_match_id"
        elif mid in seen:
            problem, code = "partido repetido en el fichero", "duplicate_match"
        elif m.external_round_id not in round_ids:
            problem, code = "jornada desconocida", "unknown_round"
        elif not m.home_team_external_id or not m.away_team_external_id:
            problem, code = "equipo sin identificador externo", "missing_team_id"
        elif m.home_team_external_id == m.away_team_external_id:
            problem, code = "local y visitante iguales", "same_team"
        elif (m.home_score is None) != (m.away_score is None):
            problem, code = "marcador parcial", "partial_score"
        elif m.status not in VALID_STATUS:
            problem, code = f"estado desconocido '{m.status}'", "invalid_status"
        if problem or not mid or not m.home_team_external_id or not m.away_team_external_id:
            plan.issues.append(Issue(code, "match", mid, problem or "partido inválido"))
            continue
        seen.add(mid)
        per_round[m.external_round_id] = per_round.get(m.external_round_id, 0) + 1
        for tid, name in ((m.home_team_external_id, m.home_team_name),
                          (m.away_team_external_id, m.away_team_name)):
            if tid not in teams or teams[tid] is None:
                teams[tid] = name
        plan.matches.append(MatchRec(
            mid, m.external_round_id, m.home_team_external_id, m.away_team_external_id,
            m.scheduled_date, m.scheduled_time,
            _scheduled_at(m.scheduled_date, m.scheduled_time, m.timezone),
            m.timezone, m.venue, m.status, m.home_score, m.away_score, m.source_url,
            content_hash(m.status, m.scheduled_date, m.scheduled_time, m.home_score, m.away_score),
        ))
    for r in league.rounds:
        got = per_round.get(r.external_round_id, 0)
        if r.matches is not None and r.matches != got:
            plan.issues.append(Issue("round_match_count", "round", r.external_round_id,
                                     f"declara {r.matches} partidos, importables {got}"))
    plan.teams = [TeamRec(k, v) for k, v in teams.items()]
    return plan
