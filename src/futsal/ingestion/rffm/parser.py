"""Parser puro de la página de jornada de RFFM (Next.js `__NEXT_DATA__`). Sin red."""

import json
import re
from datetime import UTC, date, datetime, time
from typing import Any

from futsal.ingestion.rffm.models import Match, MatchStatus, RoundData

TIMEZONE = "Europe/Madrid"
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_STATUS_KEYWORDS: tuple[tuple[str, MatchStatus], ...] = (
    ("aplaz", "postponed"),
    ("suspend", "suspended"),
    ("anula", "cancelled"),
    ("cancela", "cancelled"),
)


class RffmParseError(ValueError):
    """La página no tiene la estructura esperada."""


def extract_next_data(html: str) -> dict[str, Any]:
    m = _NEXT_DATA.search(html)
    if not m:
        raise RffmParseError("No se encontró <script id='__NEXT_DATA__'> en el HTML")
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise RffmParseError(f"__NEXT_DATA__ sin props.pageProps válido: {exc!r}") from exc
    if not isinstance(data, dict):
        raise RffmParseError("__NEXT_DATA__ no es un objeto")
    return data


def extract_page_props(html: str) -> dict[str, Any]:
    try:
        props = extract_next_data(html)["props"]["pageProps"]
    except (KeyError, TypeError) as exc:
        raise RffmParseError(f"__NEXT_DATA__ sin props.pageProps válido: {exc!r}") from exc
    if not isinstance(props, dict):
        raise RffmParseError("props.pageProps no es un objeto")
    return props


def _text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _int(value: Any) -> int | None:
    s = _text(value)
    return int(s) if s is not None and s.lstrip("-").isdigit() else None


def _date(value: Any) -> date | None:
    s = _text(value)
    if s is None:
        return None
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return None


def _time(value: Any) -> time | None:
    s = _text(value)
    if s is None:
        return None
    try:
        return datetime.strptime(s, "%H:%M").time()
    except ValueError:
        return None


def _status(raw: dict[str, Any], home: int | None, away: int | None) -> MatchStatus:
    reason = (_text(raw.get("motivo_estado")) or "").lower()
    for keyword, status in _STATUS_KEYWORDS:
        if keyword in reason:
            return status
    if home is not None and away is not None and _text(raw.get("acta_cerrada")) == "1":
        return "finished"
    if home is None and away is None and _date(raw.get("fecha")) is not None:
        return "scheduled"
    return "unknown"


def _match(raw: dict[str, Any], source_url: str) -> Match:
    home, away = _int(raw.get("Goles_casa")), _int(raw.get("Goles_visitante"))
    status_raw = ";".join(
        f"{k}={_text(raw.get(k)) or ''}" for k in ("estado", "situacion_juego", "motivo_estado")
    )
    return Match(
        external_match_id=_text(raw.get("codacta")),
        home_team_name=_text(raw.get("Nombre_equipo_local")),
        home_team_external_id=_text(raw.get("CodEquipo_local")),
        away_team_name=_text(raw.get("Nombre_equipo_visitante")),
        away_team_external_id=_text(raw.get("CodEquipo_visitante")),
        home_score=home,
        away_score=away,
        status=_status(raw, home, away),
        status_raw=status_raw,
        scheduled_date=_date(raw.get("fecha")),
        scheduled_time=_time(raw.get("hora")),
        timezone=TIMEZONE,
        venue=_text(raw.get("campojuego")),
        venue_external_id=_text(raw.get("codigo_campo")),
        match_report_url=None,  # la página no expone URL de acta (ver docs/discovery.md)
        comparison_url=None,
        source_url=source_url,
    )


def parse_round(html: str, source_url: str, observed_at: datetime | None = None) -> RoundData:
    props = extract_page_props(html)
    results = props.get("results")
    if not isinstance(results, dict) or not isinstance(results.get("partidos"), list):
        raise RffmParseError("Falta pageProps.results.partidos (estructura inesperada)")
    query = props.get("query") or {}
    season = _text(query.get("temporada")) or _text((props.get("season") or {}).get("cod_temporada"))
    round_id = _text(results.get("jornada"))
    comp_id, group_id = _text(results.get("codigo_competicion")), _text(results.get("codigo_grupo"))
    if not (season and round_id and comp_id and group_id):
        raise RffmParseError("Faltan identificadores de temporada/competición/grupo/jornada")
    return RoundData(
        external_round_id=round_id,
        round_number_or_label=_text(results.get("nombre_jornada")) or round_id,
        round_date=_date(results.get("fecha_jornada")),
        season_external_id=season,
        competition_external_id=comp_id,
        competition_name=_text(results.get("nombre_competicion")),
        group_external_id=group_id,
        group_name=_text(results.get("nombre_grupo")),
        source_url=source_url,
        observed_at=observed_at or datetime.now(UTC),
        matches=[_match(p, source_url) for p in results["partidos"] if isinstance(p, dict)],
    )
