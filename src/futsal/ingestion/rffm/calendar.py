"""Lector puro de `/competicion/calendario` (todas las jornadas de un grupo en una página). Sin red.

El calendario trae fecha, hora, campo, marcador y códigos de equipo, pero NO estado, `acta_cerrada` ni
`motivo_estado` (sondeo A1-11): el estado sale de `/api/results` (ver `results_api.py`).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from futsal.ingestion.rffm.parser import RffmParseError, extract_page_props
from futsal.ingestion.rffm.scope import ScopeCheck, validate_scope

TZ = ZoneInfo("Europe/Madrid")


@dataclass(frozen=True)
class CalendarMatch:
    codacta: str
    round_code: str
    home_team_id: str
    home_team_name: str
    away_team_id: str
    away_team_name: str
    home_score: int | None
    away_score: int | None
    scheduled_date: date | None
    scheduled_time: time | None  # None si la hora viene vacía o es «00:00»
    time_known: bool
    kickoff: datetime | None  # aware (Europe/Madrid) solo si hay fecha y hora conocidas
    venue: str | None
    venue_id: str | None


@dataclass(frozen=True)
class CalendarRound:
    code: str  # codjornada
    number: int | None
    label: str | None
    matches: list[CalendarMatch]


@dataclass(frozen=True)
class CalendarIssue:
    code: str
    detail: str
    round_code: str | None = None
    codacta: str | None = None


@dataclass
class CalendarPage:
    scope: ScopeCheck
    season_name: str | None = None
    competition_name: str | None = None
    group_name: str | None = None
    competition_format: str | None = None  # tipo_competicion (1 copa/eliminatoria, 2 liga)
    current_round: str | None = None
    rounds: list[CalendarRound] = field(default_factory=list)
    issues: list[CalendarIssue] = field(default_factory=list)

    @property
    def matches(self) -> list[CalendarMatch]:
        return [m for r in self.rounds for m in r.matches]


def _t(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _date(value: str | None) -> date | None:
    try:
        return datetime.strptime(value, "%d-%m-%Y").date() if value else None
    except ValueError:
        return None


def _time(value: str | None) -> time | None:
    """Hora conocida; vacía, «00:00» o ilegible = desconocida."""
    try:
        parsed = datetime.strptime(value, "%H:%M").time() if value else None
    except ValueError:
        return None
    return None if parsed in (None, time(0, 0)) else parsed


def _score(value: Any, issues: list[CalendarIssue], rcode: str, codacta: str | None) -> int | None:
    text = _t(value)
    if text is None:
        return None
    if not text.lstrip("-").isdigit():
        issues.append(CalendarIssue("non_numeric_score", f"marcador={text!r}", rcode, codacta))
        return None
    return int(text)


def _match(raw: dict[str, Any], rcode: str, issues: list[CalendarIssue]) -> CalendarMatch | None:
    codacta = _t(raw.get("codacta"))
    home_id, away_id = _t(raw.get("codigo_equipo_local")), _t(raw.get("codigo_equipo_visitante"))
    home, away = _t(raw.get("equipo_local")), _t(raw.get("equipo_visitante"))
    if not (home_id and home and away_id and away):
        issues.append(CalendarIssue("match_without_team", "partido sin equipo local o visitante (descanso)", rcode, codacta))
        return None
    if not codacta:
        issues.append(CalendarIssue("match_without_codacta", f"{home} - {away} sin codacta", rcode))
        return None
    day, hour = _date(_t(raw.get("fecha"))), _time(_t(raw.get("hora")))
    if _t(raw.get("fecha")) and day is None:
        issues.append(CalendarIssue("invalid_date", f"fecha={_t(raw.get('fecha'))!r}", rcode, codacta))
    elif day is None:
        issues.append(CalendarIssue("missing_date", "partido sin fecha", rcode, codacta))
    return CalendarMatch(
        codacta=codacta, round_code=rcode, home_team_id=home_id, home_team_name=home,
        away_team_id=away_id, away_team_name=away,
        home_score=_score(raw.get("goles_casa"), issues, rcode, codacta),
        away_score=_score(raw.get("goles_visitante"), issues, rcode, codacta),
        scheduled_date=day, scheduled_time=hour, time_known=hour is not None,
        kickoff=datetime.combine(day, hour, tzinfo=TZ) if day and hour else None,
        venue=_t(raw.get("campo")), venue_id=_t(raw.get("codigo_campo")),
    )


def parse_calendar(html: str, requested: dict[str, Any]) -> CalendarPage:
    """Calendario del grupo con el ámbito validado frente a `requested` (ver `scope.validate_scope`).

    Si el ámbito no coincide se devuelve una página SIN jornadas y con `scope.ok == False`: nunca se
    interpreta contenido de otro ámbito.
    """
    props = extract_page_props(html)
    scope = validate_scope(props, requested)
    page = CalendarPage(scope=scope)
    if not scope.ok:
        page.issues.append(CalendarIssue("scope_mismatch", "; ".join(scope.mismatches)))
        return page
    cal = props.get("calendar")
    if not isinstance(cal, dict) or not isinstance(cal.get("rounds"), list):
        raise RffmParseError("Falta pageProps.calendar.rounds (estructura inesperada)")
    page.season_name, page.competition_name = _t(cal.get("temporada")), _t(cal.get("competicion"))
    page.group_name, page.competition_format = _t(cal.get("grupo")), _t(cal.get("tipo_competicion"))
    page.current_round = _t(props.get("currentRound"))
    seen: set[str] = set()
    for raw_round in cal["rounds"]:
        if not isinstance(raw_round, dict) or not _t(raw_round.get("codjornada")):
            page.issues.append(CalendarIssue("round_without_code", "jornada sin codjornada"))
            continue
        code = str(_t(raw_round.get("codjornada")))
        matches: list[CalendarMatch] = []
        for raw in raw_round.get("equipos") or []:
            m = _match(raw, code, page.issues) if isinstance(raw, dict) else None
            if m is None:
                continue
            if m.codacta in seen:
                page.issues.append(CalendarIssue("duplicate_codacta", "codacta repetido", code, m.codacta))
                continue
            seen.add(m.codacta)
            matches.append(m)
        page.rounds.append(CalendarRound(code=code, number=int(code) if code.isdigit() else None,
                                         label=_t(raw_round.get("jornada")), matches=matches))
    return page
