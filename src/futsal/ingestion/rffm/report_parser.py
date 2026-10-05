"""Parser puro del acta (`/acta-partido/<codacta>`): Next.js `pageProps.game`. Sin red."""

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urljoin

from futsal.ingestion.rffm.parser import (
    TIMEZONE,
    RffmParseError,
    _date,
    _int,
    _text,
    _time,
    extract_next_data,
)
from futsal.ingestion.rffm.report_models import (
    LineupStatus,
    MatchReport,
    ReportEvent,
    ReportInfo,
    ReportMatch,
    ReportOfficial,
    ReportPlayer,
    ReportSource,
    ReportStaff,
    ReportTeam,
    Side,
    Validation,
)

TECHNICAL_SOURCE = "next_data:props.pageProps.game"
SIDES: tuple[tuple[Side, str], ...] = (("home", "local"), ("away", "visitante"))
# Listas cuya estructura no se ha observado con datos: se avisa, no se interpretan.
_UNPARSED = ("goles_penalti", "otras_tarjetas", "otros_tecnicos_local", "otros_tecnicos_visitante",
             "sustituciones_equipo_local", "sustituciones_equipo_visitante")
_LISTS = ("goles_equipo_local", "goles_equipo_visitante", "tarjetas_equipo_local",
          "tarjetas_equipo_visitante", "jugadores_equipo_local", "jugadores_equipo_visitante",
          "arbitros_partido", *_UNPARSED)


def absolute_url(value: Any, base: str) -> str | None:
    text = _text(value)
    return urljoin(base, text) if text else None


def content_hash(report: MatchReport) -> str:
    """SHA-256 del contenido normalizado (sin observed_at ni el propio hash)."""
    payload = report.model_dump(mode="json", exclude={"source": {"observed_at"}, "report": {"content_hash"}})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _rows(game: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = game.get(key)
    if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
        raise RffmParseError(f"game.{key} ausente o no es una lista de objetos")
    return value


def _date_any(value: Any) -> date | None:
    """El acta usa dd-mm-aaaa; el listado, dd/mm/aaaa."""
    text = _text(value)
    return _date(text.replace("-", "/")) if text else None


def _flag(value: Any) -> bool:
    return _text(value) == "1"


def _status(game: dict[str, Any], home: int | None, away: int | None) -> str:
    if _flag(game.get("suspendido")):
        return "suspended"
    if _flag(game.get("acta_cerrada")) and home is not None and away is not None:
        return "finished"
    return "scheduled" if home is None and away is None else "unknown"


def _players(game: dict[str, Any]) -> list[ReportPlayer]:
    out: list[ReportPlayer] = []
    for side, suffix in SIDES:
        team_id = _text(game.get(f"codigo_equipo_{suffix}"))
        for i, p in enumerate(_rows(game, f"jugadores_equipo_{suffix}"), start=1):
            name = _text(p.get("nombre_jugador"))
            if name is None:
                raise RffmParseError(f"jugador sin nombre en jugadores_equipo_{suffix}[{i}]")
            status: LineupStatus = ("starter" if _flag(p.get("titular"))
                                    else "substitute" if _flag(p.get("suplente")) else "unknown")
            out.append(ReportPlayer(
                team_side=side, team_external_id=team_id, sequence=i,
                player_external_id=_text(p.get("codjugador")), display_name=name,
                shirt_number=_int(p.get("dorsal")),
                role="goalkeeper" if _flag(p.get("portero")) else "player",
                lineup_status=status, captain=_flag(p.get("capitan"))))
    return out


def _staff(game: dict[str, Any]) -> list[ReportStaff]:
    spec: list[tuple[Side | None, str, str, str | None]] = [
        ("home", "head_coach", "entrenador_local", "cod_entrenador_local"),
        ("home", "assistant_coach", "entrenador2_local", "cod_entrenador2_local"),
        ("home", "delegate", "delegadolocal", None),
        ("away", "head_coach", "entrenador_visitante", "cod_entrenador_visitante"),
        ("away", "assistant_coach", "entrenador2_visitante", "cod_entrenador_visitante2"),
        ("away", "delegate", "delegado_visitante", None),
        (None, "field_delegate", "delegadocampo", None),
    ]
    out: list[ReportStaff] = []
    for side, role, name_key, id_key in spec:
        name = _text(game.get(name_key))
        if name is None:
            continue
        team_id = None if side is None else _text(game.get(f"codigo_equipo_{'local' if side == 'home' else 'visitante'}"))
        out.append(ReportStaff(team_side=side, team_external_id=team_id, sequence=len(out) + 1,
                               display_name=name, role=role,
                               external_id=_text(game.get(id_key)) if id_key else None))
    return out


def _officials(game: dict[str, Any]) -> list[ReportOfficial]:
    out: list[ReportOfficial] = []
    for i, o in enumerate(_rows(game, "arbitros_partido"), start=1):
        name, role = _text(o.get("nombre_arbitro")), _text(o.get("tipo_arbitro"))
        if name is None or role is None:
            raise RffmParseError(f"arbitros_partido[{i}] sin nombre o tipo")
        out.append(ReportOfficial(sequence=i, display_name=name, role=role,
                                  external_id=_text(o.get("cod_arbitro"))))
    return out


def _events(game: dict[str, Any]) -> list[ReportEvent]:
    raw: list[tuple[int | None, int, ReportEvent]] = []
    for kind, prefix in (("goal", "goles_equipo"), ("card", "tarjetas_equipo")):
        for side, suffix in SIDES:
            team_id = _text(game.get(f"codigo_equipo_{suffix}"))
            for row in _rows(game, f"{prefix}_{suffix}"):
                if kind == "goal":
                    code, detail = _text(row.get("tipo_gol")), None
                else:
                    code = _text(row.get("codigo_tipo_amonestacion"))
                    detail = {"segunda_amarilla": _text(row.get("segunda_amarilla")) or ""}
                minute = _int(row.get("minuto"))
                raw.append((minute, len(raw), ReportEvent(
                    sequence=0, event_type="goal" if kind == "goal" else "card", team_side=side,
                    team_external_id=team_id, player_external_id=_text(row.get("codjugador")),
                    player_name=_text(row.get("nombre_jugador")), minute=minute,
                    source_code=code, source_detail=detail)))
    # Cronológico por minuto; sin minuto al final; el resto conserva el orden de la fuente.
    raw.sort(key=lambda t: (t[0] is None, t[0] or 0, t[1]))
    return [e.model_copy(update={"sequence": i}) for i, (_, _, e) in enumerate(raw, start=1)]


def parse_match_report(html: str, source_url: str, observed_at: datetime | None = None) -> MatchReport:
    data = extract_next_data(html)
    try:
        game = data["props"]["pageProps"]["game"]
    except (KeyError, TypeError) as exc:
        raise RffmParseError(f"Falta props.pageProps.game (estructura inesperada): {exc!r}") from exc
    if not isinstance(game, dict):
        raise RffmParseError("props.pageProps.game no es un objeto")
    raw_query = data.get("query")
    query: dict[str, Any] = raw_query if isinstance(raw_query, dict) else {}
    codacta = _text(game.get("codacta"))
    ids = {k: _text(query.get(k)) for k in ("temporada", "competicion", "grupo")}
    round_id = _text(game.get("jornada"))
    if not (codacta and round_id and all(ids.values())):
        raise RffmParseError("Faltan codacta/jornada o identificadores de temporada/competición/grupo")
    home, away = _int(game.get("goles_local")), _int(game.get("goles_visitante"))
    host = _text(game.get("host")) or "https://appweb.rffm.es/"
    match = ReportMatch(
        home_team_name=_text(game.get("equipo_local")),
        home_team_external_id=_text(game.get("codigo_equipo_local")),
        away_team_name=_text(game.get("equipo_visitante")),
        away_team_external_id=_text(game.get("codigo_equipo_visitante")),
        scheduled_date=_date_any(game.get("fecha")), scheduled_time=_time(game.get("hora")),
        timezone=TIMEZONE, venue=_text(game.get("campo")),
        venue_external_id=_text(game.get("codigo_campo")),
        home_score=home, away_score=away, status=_status(game, home, away))
    teams = [ReportTeam(side=side, external_id=_text(game.get(f"codigo_equipo_{suf}")),
                        name=_text(game.get(f"equipo_{suf}")),
                        formation=_text(game.get(f"esquema_{suf}")),
                        crest_url=absolute_url(game.get(f"escudo_{suf}"), host))
             for side, suf in SIDES]
    for list_key in _LISTS:
        _rows(game, list_key)
    report = MatchReport(
        match=match,
        report=ReportInfo(
            report_external_id=codacta, match_external_id=codacta,
            season_external_id=str(ids["temporada"]), competition_external_id=str(ids["competicion"]),
            group_external_id=str(ids["grupo"]), round_external_id=round_id,
            closed=None if game.get("acta_cerrada") in (None, "") else _flag(game.get("acta_cerrada")),
            content_hash=""),
        teams=teams, players=_players(game), staff=_staff(game), officials=_officials(game),
        events=_events(game),
        validation=Validation(
            empty_sections=[k for k in _LISTS if k not in _UNPARSED and not game[k]],
            unparsed_sections=[f"{k} ({len(game[k])} filas)" for k in _UNPARSED if game[k]]),
        source=ReportSource(url=source_url, system="rffm", technical_source=TECHNICAL_SOURCE,
                            observed_at=observed_at or datetime.now(UTC)))
    report.report.content_hash = content_hash(report)
    return report
