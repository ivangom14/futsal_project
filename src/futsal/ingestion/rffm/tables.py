"""Clasificación y tabla de goleadores oficiales de la RFFM: modelos y parsers puros (sin red ni base de datos).

Ambas páginas llevan los datos en `__NEXT_DATA__` → `pageProps` (`standings`, `scorers`), igual que los resultados. Las reglas
que cuida este módulo, verificadas contra la web real (ver docs/phase16-tables.md):
- `standings`/`scorers` en `null` (o `group` en `null`) es un fallo intermitente de la web, NO una tabla vacía: `TransientSource`.
- Una tabla vacía válida es `estado == "1"` con la lista vacía.
- La clasificación de una jornada inexistente o futura es la actual: el llamador debe comparar `round_number` con lo pedido.
- Las eliminatorias (`tipo_competicion == "1"`) no tienen clasificación sino un cuadro: `not_applicable`.
- Faltan o cambian campos esperados → `StructureChanged`, nunca se rellena con ceros.
"""

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from futsal.ingestion.rffm.parser import RffmParseError, extract_page_props


class TransientSource(RffmParseError):
    """La web respondió 200 pero sin la tabla (`null`): se puede reintentar; no es una tabla vacía."""


class StructureChanged(RffmParseError):
    """La página no tiene los campos esperados: la estructura de la web ha cambiado o el contenido no es el pedido."""


class IdentityMismatch(RffmParseError):
    """La página corresponde a otra competición o grupo distintos del pedido."""


Outcome = Literal["ok", "empty", "not_applicable"]
STANDINGS_REQUIRED = ("posicion", "codequipo", "nombre", "jugados", "ganados", "empatados", "perdidos",
                      "goles_a_favor", "goles_en_contra", "puntos")
SCORERS_REQUIRED = ("codigo_jugador", "jugador", "codigo_equipo", "nombre_equipo", "goles")
_STANDINGS_SKIP = set(STANDINGS_REQUIRED) | {"url_img", "puntos_sancion"}
_DATE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")


class StandingRow(BaseModel):
    position: int
    team_external_id: str
    team_name: str
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    points: int
    penalty_points: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class StandingsTable(BaseModel):
    outcome: Outcome
    competition_external_id: str | None = None
    group_external_id: str | None = None
    competition_name: str | None = None
    group_name: str | None = None
    round_number: int | None = None  # jornada a la que corresponde la tabla (la que devuelve la web)
    current_round: int | None = None  # jornada actual del grupo según la web
    round_date: date | None = None
    rows: list[StandingRow] = Field(default_factory=list)
    promotions: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)  # filas descartadas u otros problemas: la tabla es incompleta


class ScorerRow(BaseModel):
    row_order: int  # orden publicado (la web no da posición explícita)
    player_external_id: str
    player_name: str
    team_external_id: str
    team_name: str
    matches_played: int | None = None
    goals: int
    penalty_goals: int | None = None
    goals_per_match: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ScorerTable(BaseModel):
    outcome: Outcome
    competition_name: str | None = None
    group_name: str | None = None
    rows: list[ScorerRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    text = _text(value)
    return int(text) if text is not None and re.fullmatch(r"-?\d+", text) else None


def _date(value: Any) -> date | None:
    m = _DATE.match(_text(value) or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _detail(value: Any) -> Any:
    if isinstance(value, list):
        return [x for x in value if x not in (None, "")]
    n = _int(value)
    return n if n is not None else _text(value)


def _props(html: str) -> dict[str, Any]:
    try:
        return extract_page_props(html)
    except RffmParseError as exc:  # sin __NEXT_DATA__ o sin pageProps: la web ya no entrega los datos como esperamos
        raise StructureChanged(str(exc)) from exc


def parse_standings(html: str, *, competition: str | None = None, group: str | None = None) -> StandingsTable:
    """Clasificación de la página. `competition`/`group`, si se dan, se comprueban contra los de la página."""
    props = _props(html)
    if "standings" not in props:
        raise StructureChanged("pageProps no incluye 'standings'")
    st = props["standings"]
    if st is None:
        raise TransientSource("la web devolvió standings=null (fallo intermitente)")
    if not isinstance(st, dict):
        raise StructureChanged("standings no es un objeto")
    if str(st.get("estado")) != "1":
        raise StructureChanged(f"standings.estado inesperado: {st.get('estado')!r}")
    got_comp, got_group = _text(st.get("codigo_competicion")), _text(st.get("codigo_grupo"))
    if (competition and got_comp != competition) or (group and got_group != group):
        raise IdentityMismatch(f"la página es de {got_comp}/{got_group}, no de {competition}/{group}")
    rounds = props.get("rounds") if isinstance(props.get("rounds"), dict) else {}
    table = StandingsTable(
        outcome="ok", competition_external_id=got_comp, group_external_id=got_group,
        competition_name=_text(st.get("competicion")), group_name=_text(st.get("grupo")),
        current_round=_int((rounds or {}).get("currentRound")), round_date=_date(st.get("fecha_jornada")))
    if "clasificacion" not in st:
        if "eliminatoria" in st:  # copas y eliminatorias: cuadro de cruces, no hay tabla de clasificación
            table.outcome = "not_applicable"
            return table
        raise StructureChanged("standings no incluye 'clasificacion' ni 'eliminatoria'")
    raw_rows = st["clasificacion"]
    if not isinstance(raw_rows, list):
        raise StructureChanged("standings.clasificacion no es una lista")
    table.round_number = _int(st.get("jornada"))
    if table.round_number is None and raw_rows:
        raise StructureChanged("standings sin 'jornada': no se puede saber a qué jornada corresponde")
    table.promotions = [{"name": _text(p.get("nombre_promocion")) or "", "color": _text(p.get("color_promocion")) or ""}
                        for p in st.get("promociones") or [] if isinstance(p, dict)]
    for index, raw in enumerate(raw_rows, start=1):
        if not isinstance(raw, dict):
            raise StructureChanged(f"fila {index} de clasificacion no es un objeto")
        missing = [k for k in STANDINGS_REQUIRED if k not in raw]
        if missing:
            raise StructureChanged(f"fila {index} sin campos esperados: {', '.join(missing)}")
        values = {k: _int(raw[k]) for k in ("posicion", "jugados", "ganados", "empatados", "perdidos",
                                            "goles_a_favor", "goles_en_contra", "puntos")}
        code, name = _text(raw["codequipo"]), _text(raw["nombre"])
        if code is None or name is None or any(v is None for v in values.values()):
            table.warnings.append(f"fila {index} descartada: valores no numéricos o equipo sin identificar")
            continue
        details = {k: _detail(v) for k, v in raw.items() if k not in _STANDINGS_SKIP and _detail(v) not in (None, "", [])}
        table.rows.append(StandingRow(
            position=values["posicion"] or 0, team_external_id=code, team_name=name, played=values["jugados"] or 0,
            won=values["ganados"] or 0, drawn=values["empatados"] or 0, lost=values["perdidos"] or 0,
            goals_for=values["goles_a_favor"] or 0, goals_against=values["goles_en_contra"] or 0,
            points=values["puntos"] or 0, penalty_points=_int(raw.get("puntos_sancion")), details=details))
    codes = [r.team_external_id for r in table.rows]
    if len(set(codes)) != len(codes):
        table.warnings.append("equipos repetidos en la clasificación")
    if not raw_rows:
        table.outcome = "empty"
    return table


def parse_scorers(html: str, *, competition: str | None = None, group: str | None = None) -> ScorerTable:
    """Tabla de goleadores de la página (solo aparecen jugadores con goles; sin posición ni paginación observadas)."""
    props = _props(html)
    if "scorers" not in props:
        raise StructureChanged("pageProps no incluye 'scorers'")
    sc, grp = props["scorers"], props.get("group")
    if sc is None or grp is None:
        raise TransientSource("la web devolvió scorers/group=null (fallo intermitente)")
    if not isinstance(sc, dict) or not isinstance(grp, dict):
        raise StructureChanged("scorers o group no son objetos")
    if str(sc.get("estado")) != "1":
        raise StructureChanged(f"scorers.estado inesperado: {sc.get('estado')!r}")
    comp = props.get("competition") if isinstance(props.get("competition"), dict) else {}
    got_group, got_comp = _text(grp.get("codigo")), _text((comp or {}).get("codigo"))
    if (group and got_group != group) or (competition and got_comp and got_comp != competition):
        raise IdentityMismatch(f"la página es de {got_comp}/{got_group}, no de {competition}/{group}")
    raw_rows = sc.get("goles")
    if not isinstance(raw_rows, list):
        raise StructureChanged("scorers.goles no es una lista")
    table = ScorerTable(outcome="ok", competition_name=_text(sc.get("competicion")), group_name=_text(sc.get("grupo")))
    for index, raw in enumerate(raw_rows, start=1):
        if not isinstance(raw, dict):
            raise StructureChanged(f"fila {index} de goles no es un objeto")
        missing = [k for k in SCORERS_REQUIRED if k not in raw]
        if missing:
            raise StructureChanged(f"fila {index} sin campos esperados: {', '.join(missing)}")
        code, name, team, team_name = (_text(raw["codigo_jugador"]), _text(raw["jugador"]), _text(raw["codigo_equipo"]),
                                       _text(raw["nombre_equipo"]))
        goals = _int(raw["goles"])
        if None in (code, name, team, team_name, goals):
            table.warnings.append(f"fila {index} descartada: jugador, equipo o goles no identificables")
            continue
        extra = {k: _detail(v) for k, v in raw.items() if k in ("foto",) and _detail(v) not in (None, "", [])}
        table.rows.append(ScorerRow(
            row_order=index, player_external_id=code or "", player_name=name or "", team_external_id=team or "",
            team_name=team_name or "", matches_played=_int(raw.get("partidos_jugados")), goals=goals or 0,
            penalty_goals=_int(raw.get("goles_penalti")), goals_per_match=_text(raw.get("goles_por_partidos")), details=extra))
    ordered = [r.goals for r in table.rows]
    if ordered != sorted(ordered, reverse=True):
        table.warnings.append("la tabla no viene ordenada por goles: orden publicado conservado")
    if any(r.goals < 1 for r in table.rows):
        table.warnings.append("hay jugadores con menos de 1 gol")
    if not raw_rows:
        table.outcome = "empty"
    return table
