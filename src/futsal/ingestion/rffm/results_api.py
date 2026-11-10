"""Lector puro de `/api/results?idGroup&round` (JSON ligero de una jornada) y conciliación con el calendario.

Los partidos de la API tienen los mismos campos que `pageProps.results.partidos` de la página de jornada, así que
el estado (`finished/scheduled/postponed...`) y `status_raw` se calculan con el parser existente, sin duplicarlo.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from futsal.ingestion.rffm.calendar import CalendarMatch
from futsal.ingestion.rffm.client import results_api_url
from futsal.ingestion.rffm.models import Match
from futsal.ingestion.rffm.parser import _match as parse_match_dict


@dataclass(frozen=True)
class ResultMatch:
    match: Match  # external_match_id = codacta; incluye estado y status_raw
    has_report: bool  # hay_actas
    report_closed: bool  # acta_cerrada
    in_play: bool  # partido_en_juego


@dataclass(frozen=True)
class RoundResult:
    competition_id: str | None
    group_id: str
    round_id: str
    round_name: str | None
    matches: list[ResultMatch]
    issues: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Rejected:
    """Respuesta no utilizable. `null` es transitorio (el reintento lo trata como tal); el resto, no."""
    reason: str  # null | not_object | group_mismatch | round_mismatch | no_matches
    detail: str = ""

    @property
    def transient(self) -> bool:
        return self.reason == "null"


def _t(value: Any) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def parse_results_api(payload: Any, requested_group: str, requested_round: int | str) -> RoundResult | Rejected:
    """Jornada de la API validada frente al grupo y la jornada pedidos. `payload`: JSON ya cargado o texto JSON."""
    if isinstance(payload, (str, bytes)):
        text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
        if not text.strip():
            return Rejected("null", "cuerpo vacío")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            return Rejected("not_object", f"JSON inválido: {exc}")
    if payload is None:
        return Rejected("null", "cuerpo null")
    if not isinstance(payload, dict):
        return Rejected("not_object", f"tipo {type(payload).__name__}")
    group, rnd = _t(payload.get("codigo_grupo")), _t(payload.get("jornada"))
    if group != str(requested_group).strip():
        return Rejected("group_mismatch", f"codigo_grupo={group!r} != {str(requested_group)!r}")
    if rnd != str(requested_round).strip():
        return Rejected("round_mismatch", f"jornada={rnd!r} != {str(requested_round)!r}")
    raw = payload.get("partidos")
    if not isinstance(raw, list):
        return Rejected("no_matches", "falta la lista `partidos`")
    url = results_api_url(group, rnd)
    matches: list[ResultMatch] = []
    issues: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            issues.append("partido no es un objeto")
            continue
        m = parse_match_dict(item, url)
        if not m.external_match_id:
            issues.append("partido sin codacta")
            continue
        matches.append(ResultMatch(match=m, has_report=_t(item.get("hay_actas")) == "1",
                                   report_closed=_t(item.get("acta_cerrada")) == "1",
                                   in_play=_t(item.get("partido_en_juego")) == "1"))
    return RoundResult(competition_id=_t(payload.get("codigo_competicion")), group_id=group, round_id=rnd,
                       round_name=_t(payload.get("nombre_jornada")), matches=matches, issues=issues)


@dataclass(frozen=True)
class Discrepancy:
    field: str  # home_score | away_score | scheduled_date | scheduled_time
    calendar: object
    results: object


@dataclass(frozen=True)
class Reconciliation:
    codacta: str
    consistent: bool
    discrepancies: list[Discrepancy]
    preferred: str = "results"  # prevalece /api/results (trae estado y acta)


def reconcile_calendar_results(calendar_match: CalendarMatch, results_match: ResultMatch | Match) -> Reconciliation:
    """Compara marcador, fecha y hora del calendario con `/api/results`; solo informa (no escribe en BD).

    La hora solo se compara si ambas fuentes la conocen («00:00» o vacía = desconocida en el calendario).
    """
    r = results_match.match if isinstance(results_match, ResultMatch) else results_match
    pairs: list[tuple[str, object, object]] = [
        ("home_score", calendar_match.home_score, r.home_score),
        ("away_score", calendar_match.away_score, r.away_score),
        ("scheduled_date", calendar_match.scheduled_date, r.scheduled_date),
    ]
    if calendar_match.time_known and r.scheduled_time is not None:
        pairs.append(("scheduled_time", calendar_match.scheduled_time, r.scheduled_time))
    diffs = [Discrepancy(name, a, b) for name, a, b in pairs if a != b]
    return Reconciliation(codacta=calendar_match.codacta, consistent=not diffs, discrepancies=diffs)
