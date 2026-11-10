"""Registro en proceso de las herramientas v2 (entrega 1): consultas sin grupo que llaman a `repositories/v2_queries.py` sin HTTP ni stdio.

Cada herramienta abre su propia `Session`, así que varias pueden ejecutarse en paralelo (hilos). Devuelven el mismo JSON que el endpoint `/v2/...`
equivalente (`items`, `count`, `shown`, `notes`, `freshness`); el agente lo recorta después con `compact_result` como cualquier otro resultado.
Las mismas funciones tipadas las publica `mcp_server/v2.py` con FastMCP (una sola fuente de esquemas).
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Annotated, Any, Literal

from fastapi.encoders import jsonable_encoder
from mcp.server.fastmcp.tools import Tool
from pydantic import Field, ValidationError, validate_call
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from futsal.agent.mcp_client import strip_titles
from futsal.db.session import make_engine
from futsal.domain.dates import MADRID
from futsal.domain.names import norm_name, tokens_of
from futsal.repositories import v2_queries as q

Name = Annotated[str, Field(min_length=2, max_length=80, description="Nombre o parte del nombre")]
Category = Literal["senior", "juvenil", "cadete", "infantil", "alevin", "benjamin", "prebenjamin", "veteranos", "universitario"]
Gender = Literal["male", "female", "mixed"]
Metric = Literal["goals_for", "goals_against", "wins", "points_per_game"]
Limit = Annotated[int, Field(ge=1, le=q.MAX_LIMIT, description="Máximo de filas (1-100, def. 20)")]
Today = Callable[[], date]
SessionFactory = Callable[[], Session]


def madrid_today() -> date:
    return datetime.now(MADRID).date()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    fn: Callable[..., dict[str, Any]]


def _parse_day(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise q.InvalidParam(f"{field} debe ser AAAA-MM-DD") from exc


def _parse_time(value: str | None, field: str) -> time | None:
    if value is None or not value.strip():
        return None
    try:
        return time.fromisoformat(value.strip())
    except ValueError as exc:
        raise q.InvalidParam(f"{field} debe ser HH:MM") from exc


def _only_team(body: dict[str, Any], team: str) -> dict[str, Any]:
    """`find_player(team=…)`: se queda con los jugadores cuyo equipo contiene todas las palabras pedidas (la API v2 no filtra por equipo)."""
    words = tokens_of(team)
    items = [i for i in body["items"] if all(w in norm_name(str(i["team"]["name"])).split() for w in words)]
    notes = list(body["notes"])
    if body["items"] and not items:
        teams = ", ".join(sorted({str(i["team"]["name"]) for i in body["items"]}))
        notes.append(f"hay jugadores con ese nombre pero ninguno en un equipo que coincida con «{team}»; figuran en: {teams}")
    elif len(items) < len(body["items"]):
        notes.append(f"filtrado por equipo «{team}»")
    return {**body, "items": items, "count": len(items), "shown": len(items), "notes": notes}


class V2Registry:
    """Las 7 herramientas v2. `session_factory` y `today` se inyectan (tests, servidor MCP)."""

    def __init__(self, session_factory: SessionFactory, today: Today = madrid_today) -> None:
        self._session_factory = session_factory
        self._today = today
        self._specs = self._build()
        self._by_name = {s.name: s for s in self._specs}
        self._validated = {s.name: validate_call(s.fn) for s in self._specs}

    # ------------------------------------------------------------------------------------------------------ consultas
    def _query(self, fn: Callable[[Session, date], dict[str, Any]]) -> dict[str, Any]:
        with self._session_factory() as db:
            return fn(db, self._today())

    def _build(self) -> list[ToolSpec]:
        reg = self

        def find_player(name: Name, team: Annotated[str | None, Field(max_length=80, description="Equipo (opcional)")] = None,
                        limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
            def run(db: Session, today: date) -> dict[str, Any]:
                body = q.players(db, q.build_scope(db, today, q.Filters()), name, q.MAX_LIMIT if team else limit)
                if not (team and body["items"]):
                    return body
                body = _only_team(body, team)
                return {**body, "items": body["items"][:limit], "shown": min(body["shown"], limit)}
            return reg._query(run)

        def find_official(name: Name, include_matches: Annotated[bool, Field(description="Incluir la lista de partidos pitados")] = False,
                          limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
            return reg._query(lambda db, today: q.officials(db, q.build_scope(db, today, q.Filters()), name, include_matches, limit))

        def officials_ranking(role: Annotated[str, Field(min_length=2, max_length=40, description="Rol, p. ej. ARBITRO")] = "ARBITRO",
                              category: Category | None = None, limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
            return reg._query(lambda db, today: q.officials_ranking(db, q.build_scope(db, today, q.Filters(None, category)), role, limit))

        def matches_by_date(
                date_from: Annotated[str, Field(description="AAAA-MM-DD")],
                date_to: Annotated[str | None, Field(description="AAAA-MM-DD; por defecto date_from")] = None,
                time_from: Annotated[str | None, Field(description="HH:MM")] = None, time_to: Annotated[str | None, Field(description="HH:MM")] = None,
                category: Category | None = None, gender: Gender | None = None,
                competition: Annotated[str | None, Field(max_length=80, description="Parte del nombre de la competición")] = None,
                venue: Annotated[str | None, Field(min_length=2, max_length=80, description="Parte del nombre de la pista")] = None,
                limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
            start = _parse_day(date_from, "date_from")
            end = _parse_day(date_to, "date_to") if date_to else start
            t0, t1 = _parse_time(time_from, "time_from"), _parse_time(time_to, "time_to")
            return reg._query(lambda db, today: q.matches(
                db, q.build_scope(db, today, q.Filters(None, category, gender, competition)), date_from=start, date_to=end, time_from=t0,
                time_to=t1, venue=venue, status=None, order="asc", limit=limit))

        def club_overview(name: Name, results: Annotated[bool, Field(description="Incluir resultados de cada equipo")] = False,
                          category: Category | None = None, limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
            def run(db: Session, today: date) -> dict[str, Any]:
                scope = q.build_scope(db, today, q.Filters(None, category))
                return (q.clubs_results if results else q.clubs_search)(db, scope, name, limit)
            return reg._query(run)

        def scorers_ranking(category: Category | None = None, gender: Gender | None = None,
                            limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
            return reg._query(lambda db, today: q.scorers_ranking(
                db, q.build_scope(db, today, q.Filters(None, category, gender), kind="league", drop_unknown_category=True), limit))

        def teams_ranking(metric: Metric, category: Category | None = None, gender: Gender | None = None,
                          limit: Limit = q.DEFAULT_LIMIT) -> dict[str, Any]:
            return reg._query(lambda db, today: q.teams_ranking(
                db, q.build_scope(db, today, q.Filters(None, category, gender), drop_unknown_category=True), metric, None, 1, limit))

        return [
            ToolSpec("find_player", "Jugador por nombre en todas las competiciones (equipo, grupo, actas); sin coincidencia, sugerencias.", find_player),
            ToolSpec("find_official", "Árbitro por nombre en todas las competiciones; con include_matches, sus partidos.", find_official),
            ToolSpec("officials_ranking", "Árbitros con más partidos pitados.", officials_ranking),
            ToolSpec("matches_by_date", "Partidos de un día o rango y franja horaria en todos los grupos.", matches_by_date),
            ToolSpec("club_overview", "Todos los equipos de un club (A/B, categorías) y, con results, sus resultados.", club_overview),
            ToolSpec("scorers_ranking", "Máximos goleadores de las ligas (tablas oficiales), filtrables por categoría.", scorers_ranking),
            ToolSpec("teams_ranking", "Ranking de equipos: goles a favor/en contra, victorias, puntos por partido.", teams_ranking),
        ]

    # ------------------------------------------------------------------------------------------------------ catálogo
    @property
    def specs(self) -> list[ToolSpec]:
        return list(self._specs)

    def handles(self, name: str) -> bool:
        return name in self._by_name

    def tool_defs(self) -> list[dict[str, Any]]:
        """`[{name, description, input_schema}]` compactos: sin `title`, sin `default: null` ni `anyOf[X, null]`, descripción de una línea."""
        return [{"name": s.name, "description": " ".join(s.description.split()),
                 "input_schema": _compact(strip_titles(Tool.from_function(s.fn, name=s.name, description=s.description).parameters))}
                for s in self._specs]

    # ------------------------------------------------------------------------------------------------------ ejecución
    def _run_sync(self, name: str, args: dict[str, Any]) -> str:
        body = self._validated[name](**args)
        return json.dumps(jsonable_encoder(body), ensure_ascii=False, separators=(",", ":"))

    async def call(self, name: str, args: dict[str, Any]) -> tuple[bool, str]:
        """(es_error, texto). Los errores usan los mismos prefijos que el servidor MCP v1."""
        try:
            return False, await asyncio.to_thread(self._run_sync, name, args)
        except ValidationError as exc:
            detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
            return True, f"error_validacion: parámetros inválidos ({detail[:200]})"
        except q.InvalidParam as exc:
            return True, f"error_validacion: parámetros inválidos ({str(exc)[:200]})"
        except Exception as exc:  # noqa: BLE001 - el error vuelve al LLM sin detalles técnicos
            return True, f"error_dependencia: {type(exc).__name__}"


def _compact(node: Any) -> Any:
    if isinstance(node, dict):
        any_of = node.get("anyOf")
        if isinstance(any_of, list):
            real = [x for x in any_of if not (isinstance(x, dict) and x.get("type") == "null")]
            if len(real) == 1 and isinstance(real[0], dict):
                return _compact({**{k: v for k, v in node.items() if k != "anyOf"}, **real[0]})
        return {k: _compact(v) for k, v in node.items() if not (k == "default" and v is None)}
    if isinstance(node, list):
        return [_compact(x) for x in node]
    return node


_default_engine: Engine | None = None


def _default_session() -> Session:
    global _default_engine
    if _default_engine is None:
        _default_engine = make_engine()
    return Session(_default_engine)


def default_registry() -> V2Registry:
    """Registro contra la base de datos de `DATABASE_URL` (el motor se crea en la primera consulta)."""
    return V2Registry(_default_session)
