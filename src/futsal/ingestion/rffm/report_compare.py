"""Comparación pura entre un partido ya importado y su acta."""

from dataclasses import dataclass
from datetime import date, time

from futsal.ingestion.rffm.report_models import ComparisonRow, MatchReport


@dataclass(frozen=True)
class DbMatchView:
    """Lo que PostgreSQL sabe del partido (valores del listado de jornadas)."""

    external_id: str
    competition_external_id: str
    group_external_id: str
    round_external_id: str
    home_team_external_id: str
    home_team_name: str | None
    away_team_external_id: str
    away_team_name: str | None
    home_score: int | None
    away_score: int | None
    scheduled_date: date | None
    scheduled_time: time | None
    venue: str | None


def _s(value: object) -> str | None:
    return None if value is None else str(value)


def compare(report: MatchReport, db: DbMatchView) -> list[ComparisonRow]:
    m, r = report.match, report.report
    pairs: list[tuple[str, object, object]] = [
        ("competición", db.competition_external_id, r.competition_external_id),
        ("grupo", db.group_external_id, r.group_external_id),
        ("jornada", db.round_external_id, r.round_external_id),
        ("equipo local (id)", db.home_team_external_id, m.home_team_external_id),
        ("equipo local (nombre)", db.home_team_name, m.home_team_name),
        ("equipo visitante (id)", db.away_team_external_id, m.away_team_external_id),
        ("equipo visitante (nombre)", db.away_team_name, m.away_team_name),
        ("goles local", db.home_score, m.home_score),
        ("goles visitante", db.away_score, m.away_score),
        ("fecha", db.scheduled_date, m.scheduled_date),
        ("hora", db.scheduled_time, m.scheduled_time),
        ("campo", db.venue, m.venue),
    ]
    return [ComparisonRow(field=f, matches_value=_s(a), report_value=_s(b), equal=a == b)
            for f, a, b in pairs]


# Diferencias que invalidan la importación (el acta no corresponde a ese partido).
MATERIAL_FIELDS = {"competición", "grupo", "jornada", "equipo local (id)", "equipo visitante (id)"}


def material_errors(rows: list[ComparisonRow]) -> list[str]:
    return [f"{r.field}: matches={r.matches_value} acta={r.report_value}"
            for r in rows if r.field in MATERIAL_FIELDS and not r.equal]


def discrepancies(rows: list[ComparisonRow]) -> list[ComparisonRow]:
    return [r for r in rows if not r.equal and r.field not in MATERIAL_FIELDS]


def events_score(report: MatchReport) -> tuple[int, int]:
    """Marcador que resulta de los goles: el gol en propia suma al equipo rival."""
    home = away = 0
    for e in report.events:
        if e.event_type == "card":
            continue
        credited = e.team_side if e.event_type == "goal" else ("away" if e.team_side == "home" else "home")
        if credited == "home":
            home += 1
        else:
            away += 1
    return home, away
