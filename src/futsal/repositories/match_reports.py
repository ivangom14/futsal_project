"""Persistencia de actas: observaciones versionadas por hash y upsert idempotente de personas."""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.orm import Session, aliased

from futsal.db.models import (
    Competition,
    CompetitionGroup,
    Match,
    MatchEvent,
    MatchObservation,
    MatchOfficial,
    MatchPlayer,
    MatchReport,
    MatchReportObservation,
    MatchStaff,
    Player,
    PlayerTeamMembership,
    Round,
    Season,
    Team,
)
from futsal.importer.transform import SOURCE, content_hash
from futsal.ingestion.rffm.report_compare import (
    DbMatchView,
    compare,
    discrepancies,
    events_score,
    material_errors,
)
from futsal.ingestion.rffm.report_models import MatchReport as ReportModel

Stats = dict[str, Counter[str]]
TABLES = ("match_reports", "match_report_observations", "players", "player_team_memberships",
          "match_players", "match_staff", "match_officials", "match_events")


class ReportImportError(RuntimeError):
    """Error material: el acta no corresponde al partido almacenado. Provoca rollback."""


@dataclass(frozen=True)
class Candidate:
    external_id: str
    round_label: str
    home: str | None
    away: str | None
    score: str
    scheduled_date: str
    season_external_id: str
    competition_external_id: str
    group_external_id: str


@dataclass(frozen=True)
class Loaded:
    match: Match
    view: DbMatchView
    season_id: int
    season_external_id: str


def select_candidates(
    session: Session, group_external_id: str, limit: int | None = None
) -> list[Candidate]:
    home, away = aliased(Team), aliased(Team)
    stmt = (
        select(Match, Round, home, away, CompetitionGroup, Competition, Season)
        .join(Round, (Round.id == Match.round_id) & (Round.group_id == Match.group_id))
        .join(home, home.id == Match.home_team_id).join(away, away.id == Match.away_team_id)
        .join(CompetitionGroup, CompetitionGroup.id == Match.group_id)
        .join(Competition, Competition.id == CompetitionGroup.competition_id)
        .join(Season, Season.id == Competition.season_id)
        .outerjoin(MatchReport, MatchReport.match_id == Match.id)
        .where(CompetitionGroup.external_id == group_external_id, Match.status == "finished",
               Match.home_score.is_not(None), Match.away_score.is_not(None),
               MatchReport.id.is_(None))
        .order_by(cast(Round.external_id, Integer), Match.external_id))
    if limit is not None:
        stmt = stmt.limit(limit)
    return [Candidate(m.external_id, rnd.visible_label, h.name, a.name,
                      f"{m.home_score}-{m.away_score}", str(m.scheduled_date),
                      s.external_id, c.external_id, g.external_id)
            for m, rnd, h, a, g, c, s in session.execute(stmt).all()]


def select_candidate(session: Session, group_external_id: str) -> Candidate | None:
    found = select_candidates(session, group_external_id, 1)
    return found[0] if found else None


def load_match(session: Session, external_id: str) -> Loaded | None:
    home, away = aliased(Team), aliased(Team)
    row = session.execute(
        select(Match, Round, home, away, CompetitionGroup, Competition, Season)
        .join(Round, (Round.id == Match.round_id) & (Round.group_id == Match.group_id))
        .join(home, home.id == Match.home_team_id).join(away, away.id == Match.away_team_id)
        .join(CompetitionGroup, CompetitionGroup.id == Match.group_id)
        .join(Competition, Competition.id == CompetitionGroup.competition_id)
        .join(Season, Season.id == Competition.season_id)
        .where(Match.source == SOURCE, Match.external_id == external_id)).first()
    if row is None:
        return None
    m, rnd, h, a, g, c, s = row
    view = DbMatchView(m.external_id, c.external_id, g.external_id, rnd.external_id,
                       h.external_id, h.name, a.external_id, a.name, m.home_score, m.away_score,
                       m.scheduled_date, m.scheduled_time, m.venue)
    return Loaded(m, view, s.id, s.external_id)


def _ensure_player(session: Session, ext_id: str, name: str, now: datetime, stats: Stats) -> Player:
    p = session.scalars(select(Player).filter_by(source=SOURCE, external_id=ext_id)).one_or_none()
    if p is None:
        p = Player(source=SOURCE, external_id=ext_id, display_name=name, first_seen_at=now,
                   last_seen_at=now)
        session.add(p)
        session.flush()
        stats["players"]["inserted"] += 1
        return p
    p.last_seen_at = now
    if p.display_name != name:
        p.display_name = name
        stats["players"]["updated"] += 1
    else:
        stats["players"]["unchanged"] += 1
    return p


def _membership(session: Session, p: Player, team_id: int, season_id: int, now: datetime,
                stats: Stats) -> None:
    ms = session.scalars(select(PlayerTeamMembership).filter_by(
        player_id=p.id, team_id=team_id, season_id=season_id)).one_or_none()
    if ms is None:
        session.add(PlayerTeamMembership(player_id=p.id, team_id=team_id, season_id=season_id,
                                         first_seen_at=now, last_seen_at=now))
        stats["player_team_memberships"]["inserted"] += 1
    else:
        ms.last_seen_at = now
        stats["player_team_memberships"]["unchanged"] += 1


def _count(session: Session, model: Any, obs_id: int) -> int:
    stmt = select(func.count()).select_from(model).where(model.observation_id == obs_id)
    return int(session.execute(stmt).scalar_one())


def _reconcile_score(session: Session, match: Match, report: ReportModel, run_id: int,
                     now: datetime, stats: Stats) -> list[tuple[str, str]]:
    """Si el acta está cerrada y su marcador difiere, el acta prevalece. El valor anterior
    se conserva en `match_observations`. Idempotente: tras aplicarse ya no hay diferencia."""
    rm = report.match
    if (not report.report.closed or rm.status != "finished" or match.status != "finished"
            or rm.home_score is None or rm.away_score is None):
        return []
    old = (match.home_score, match.away_score)
    if old == (rm.home_score, rm.away_score):
        return []
    match.home_score, match.away_score = rm.home_score, rm.away_score
    digest = content_hash(match.status, match.scheduled_date, match.scheduled_time,
                          match.home_score, match.away_score)
    last = session.scalars(select(MatchObservation.content_hash).where(
        MatchObservation.match_id == match.id)
        .order_by(MatchObservation.observed_at.desc(), MatchObservation.id.desc()).limit(1)
    ).one_or_none()
    if last != digest:
        session.add(MatchObservation(
            match_id=match.id, ingestion_run_id=run_id, observed_at=now, status=match.status,
            home_score=match.home_score, away_score=match.away_score,
            scheduled_date=match.scheduled_date, scheduled_time=match.scheduled_time,
            scheduled_at=match.scheduled_at, content_hash=digest))
        stats.setdefault("match_observations", Counter())["inserted"] += 1
    stats.setdefault("matches", Counter())["updated"] += 1
    session.flush()
    return [("report_score_applied",
             f"marcador: matches={old[0]}-{old[1]} -> acta={rm.home_score}-{rm.away_score}. "
             "El acta cerrada prevalece sobre el listado; el valor anterior queda en "
             "match_observations.")]


def import_report(session: Session, report: ReportModel, run_id: int, now: datetime
                  ) -> tuple[Stats, list[tuple[str, str]]]:
    """Devuelve (estadísticas por tabla, incidencias [(código, mensaje)]). Lanza ReportImportError."""
    stats: Stats = {t: Counter() for t in TABLES}
    loaded = load_match(session, report.report.match_external_id)
    if loaded is None:
        raise ReportImportError(f"el partido {report.report.match_external_id} no existe en PostgreSQL")
    errors = material_errors(compare(report, loaded.view))
    if report.report.season_external_id != loaded.season_external_id:
        errors.append(f"temporada: matches={loaded.season_external_id} "
                      f"acta={report.report.season_external_id}")
    if errors:
        raise ReportImportError("el acta no corresponde al partido: " + "; ".join(errors))
    m = loaded.match
    team_by_side = {"home": m.home_team_id, "away": m.away_team_id}
    applied = _reconcile_score(session, m, report, run_id, now, stats)
    rows = compare(report, replace(loaded.view, home_score=m.home_score, away_score=m.away_score))

    rep = session.scalars(select(MatchReport).filter_by(
        source=SOURCE, external_id=report.report.report_external_id)).one_or_none()
    if rep is None:
        rep = MatchReport(source=SOURCE, external_id=report.report.report_external_id,
                          match_id=m.id, source_url=report.source.url, first_seen_at=now,
                          last_seen_at=now)
        session.add(rep)
        session.flush()
        stats["match_reports"]["inserted"] += 1
    else:
        rep.last_seen_at = now
        stats["match_reports"]["unchanged"] += 1

    # personas: idempotentes por identificador externo, independientes de la versión del acta
    people: dict[str, Player] = {}
    for pl in report.players:
        if pl.player_external_id:
            p = _ensure_player(session, pl.player_external_id, pl.display_name, now, stats)
            people[pl.player_external_id] = p
            _membership(session, p, team_by_side[pl.team_side], loaded.season_id, now, stats)
    for ev in report.events:
        if ev.player_external_id and ev.player_name and ev.player_external_id not in people:
            people[ev.player_external_id] = _ensure_player(
                session, ev.player_external_id, ev.player_name, now, stats)

    existing = session.scalars(select(MatchReportObservation).filter_by(
        report_id=rep.id, content_hash=report.report.content_hash)).one_or_none()
    if existing is not None:
        stats["match_report_observations"]["unchanged"] += 1
        for key, model in (("match_players", MatchPlayer), ("match_staff", MatchStaff),
                           ("match_officials", MatchOfficial), ("match_events", MatchEvent)):
            stats[key]["unchanged"] += _count(session, model, existing.id)
        return stats, applied

    teams = {t.side: t for t in report.teams}
    obs = MatchReportObservation(
        report_id=rep.id, ingestion_run_id=run_id, observed_at=report.source.observed_at,
        content_hash=report.report.content_hash, source_url=report.source.url,
        closed=report.report.closed, status=report.match.status,
        round_external_id=report.report.round_external_id,
        home_team_external_id=report.match.home_team_external_id,
        away_team_external_id=report.match.away_team_external_id,
        home_formation=teams["home"].formation if "home" in teams else None,
        away_formation=teams["away"].formation if "away" in teams else None,
        home_score=report.match.home_score, away_score=report.match.away_score,
        scheduled_date=report.match.scheduled_date, scheduled_time=report.match.scheduled_time,
        venue=report.match.venue)
    session.add(obs)
    session.flush()
    stats["match_report_observations"]["inserted"] += 1
    if rep.source_url != report.source.url:
        rep.source_url = report.source.url
    for pl in report.players:
        session.add(MatchPlayer(
            observation_id=obs.id, team_id=team_by_side[pl.team_side],
            player_id=people[pl.player_external_id].id if pl.player_external_id else None,
            player_external_id=pl.player_external_id, display_name=pl.display_name,
            shirt_number=pl.shirt_number, role=pl.role, lineup_status=pl.lineup_status,
            is_captain=pl.captain, sequence=pl.sequence))
        stats["match_players"]["inserted"] += 1
    for st in report.staff:
        session.add(MatchStaff(
            observation_id=obs.id, team_id=team_by_side[st.team_side] if st.team_side else None,
            display_name=st.display_name, role=st.role, external_id=st.external_id,
            sequence=st.sequence))
        stats["match_staff"]["inserted"] += 1
    for of in report.officials:
        session.add(MatchOfficial(observation_id=obs.id, display_name=of.display_name,
                                  role=of.role, external_id=of.external_id, sequence=of.sequence))
        stats["match_officials"]["inserted"] += 1
    for ev in report.events:
        session.add(MatchEvent(
            observation_id=obs.id, sequence=ev.sequence, event_type=ev.event_type,
            team_id=team_by_side[ev.team_side],
            player_id=people[ev.player_external_id].id if ev.player_external_id else None,
            player_external_id=ev.player_external_id, player_name=ev.player_name,
            minute=ev.minute, source_code=ev.source_code, source_detail=ev.source_detail))
        stats["match_events"]["inserted"] += 1
    session.flush()

    issues = applied + [("report_value_mismatch",
               f"{d.field}: matches={d.matches_value} acta={d.report_value}. Se conserva el valor de "
               "matches (listado de jornadas) y el del acta queda en la observación; no se "
               "consolida automáticamente (el acta no está cerrada o no es un partido finalizado).") for d in discrepancies(rows)]
    derived = events_score(report)
    if report.match.home_score is not None and derived != (report.match.home_score,
                                                           report.match.away_score):
        issues.append(("report_events_score_mismatch",
                       f"los goles del acta (propias incluidas) dan {derived[0]}-{derived[1]} y el "
                       f"marcador del acta es {report.match.home_score}-{report.match.away_score}"))
    issues += [("report_section_not_parsed", f"sección sin interpretar: {s}")
               for s in report.validation.unparsed_sections]
    return stats, issues
