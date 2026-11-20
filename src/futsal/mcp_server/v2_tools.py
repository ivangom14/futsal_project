"""Herramientas v2 del servidor MCP (consultas sin grupo): capa fina sobre la API `/v2`, igual que las de v1 sobre la API v1.

Arquitectura obligatoria: agente → MCP → API → BD. Este módulo NO accede a la base de datos: cada herramienta traduce sus parámetros
a una petición GET de la API y devuelve su JSON (`items`, `count`, `shown`, `notes`, `freshness`). Mismos nombres, parámetros y
descripciones que el antiguo registro en proceso (`agent/v2/registry.py`, retirado), así que el contrato con el LLM no cambia.
"""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from futsal.domain.names import norm_name, tokens_of
from futsal.mcp_server.sport import SportArg

V2_TOOL_NAMES = ("find_player", "find_official", "officials_ranking", "matches_by_date", "club_overview", "scorers_ranking", "teams_ranking")
API_MAX_LIMIT = 100  # tope de `limit` de la API /v2 (repositories.v2_queries.MAX_LIMIT; la API lo valida con 422)
API_DEFAULT_LIMIT = 20
PAGE_SIZE = 10  # listados (partidos, rankings, partidos de un árbitro): primera página de 10; `count` trae el total exacto

Name = Annotated[str, Field(min_length=2, max_length=80, description="Nombre o parte del nombre")]
Category = Literal["senior", "juvenil", "cadete", "infantil", "alevin", "benjamin", "prebenjamin", "veteranos", "universitario"]
Gender = Literal["male", "female", "mixed"]
Metric = Literal["goals_for", "goals_against", "wins", "points_per_game"]
Limit = Annotated[int, Field(ge=1, le=API_MAX_LIMIT, description="Máximo de filas (1-100, def. 20)")]
PageLimit = Annotated[int, Field(ge=1, le=API_MAX_LIMIT, description="Filas de la página (def. 10; count = total exacto)")]
Offset = Annotated[int, Field(ge=0, le=10_000, description="Desde qué fila (paginación; next_offset = la siguiente)")]
Get = Callable[..., dict[str, Any]]  # (ruta, recurso, **parámetros) -> JSON de la API; lanza ToolError con el prefijo de error


def only_team(body: dict[str, Any], team: str) -> dict[str, Any]:
    """`find_player(team=…)`: jugadores cuyo equipo contiene todas las palabras pedidas (la API /v2/players no filtra por equipo).
    Es un filtro de presentación sobre la respuesta de la API, no un cálculo."""
    words = tokens_of(team)
    items = [i for i in body["items"] if all(w in norm_name(str(i["team"]["name"])).split() for w in words)]
    notes = list(body.get("notes") or [])
    if body["items"] and not items:
        teams = ", ".join(sorted({str(i["team"]["name"]) for i in body["items"]}))
        notes.append(f"hay jugadores con ese nombre pero ninguno en un equipo que coincida con «{team}»; figuran en: {teams}")
    elif len(items) < len(body["items"]):
        notes.append(f"filtrado por equipo «{team}»")
    return {**body, "items": items, "count": len(items), "shown": len(items), "notes": notes}


def register_v2_tools(mcp: FastMCP, get: Get) -> None:
    """Registra las 7 herramientas v2 en `mcp`. `get` hace la petición a la API (el mismo cliente HTTP que las herramientas v1)."""

    @mcp.tool(description="Jugador por nombre en todas las competiciones (equipo, grupo, actas); sin coincidencia, sugerencias.")
    def find_player(sport: SportArg, name: Name, team: Annotated[str | None, Field(max_length=80, description="Equipo (opcional)")] = None,
                    limit: Limit = API_DEFAULT_LIMIT) -> dict[str, Any]:
        body = get("/v2/players", "jugadores", sport=sport, name=name, limit=API_MAX_LIMIT if team else limit)
        if not (team and body["items"]):
            return body
        body = only_team(body, team)
        return {**body, "items": body["items"][:limit], "shown": min(body["shown"], limit)}

    @mcp.tool(description="Árbitro por nombre en todas las competiciones; con include_matches, sus partidos (paginados, orden cronológico).")
    def find_official(sport: SportArg, name: Name, include_matches: Annotated[bool, Field(description="Incluir la lista de partidos pitados")] = False,
                      limit: Limit = API_DEFAULT_LIMIT, offset: Offset = 0) -> dict[str, Any]:
        if include_matches:  # página real en la BD; varios árbitros con ese nombre → `people`, sin mezclar sus partidos
            return get("/v2/officials/matches", "árbitros", sport=sport, name=name, limit=min(limit, PAGE_SIZE) if limit == API_DEFAULT_LIMIT else limit,
                       offset=offset)
        return get("/v2/officials", "árbitros", sport=sport, name=name, limit=limit)

    @mcp.tool(description="Árbitros con más partidos pitados.")
    def officials_ranking(sport: SportArg, role: Annotated[str, Field(min_length=2, max_length=40, description="Rol, p. ej. ARBITRO")] = "ARBITRO",
                          category: Category | None = None, limit: PageLimit = PAGE_SIZE, offset: Offset = 0) -> dict[str, Any]:
        return get("/v2/officials/ranking", "árbitros", sport=sport, role=role, category=category, limit=limit, offset=offset)

    @mcp.tool(description="Partidos de un día o rango y franja horaria en todos los grupos (orden cronológico; páginas de 10, count = total).")
    def matches_by_date(
            sport: SportArg,
            date_from: Annotated[str, Field(description="AAAA-MM-DD")],
            date_to: Annotated[str | None, Field(description="AAAA-MM-DD; por defecto date_from")] = None,
            time_from: Annotated[str | None, Field(description="HH:MM")] = None, time_to: Annotated[str | None, Field(description="HH:MM")] = None,
            category: Category | None = None, gender: Gender | None = None,
            competition: Annotated[str | None, Field(max_length=80, description="Parte del nombre de la competición")] = None,
            venue: Annotated[str | None, Field(min_length=2, max_length=80, description="Parte del nombre de la pista")] = None,
            limit: PageLimit = PAGE_SIZE, offset: Offset = 0) -> dict[str, Any]:
        return get("/v2/matches", "partidos", sport=sport, date_from=date_from.strip(), date_to=(date_to or date_from).strip(),
                   time_from=(time_from or None) and time_from.strip(), time_to=(time_to or None) and time_to.strip(),
                   category=category, gender=gender, competition=competition, venue=venue, order="asc", limit=limit, offset=offset, compact=True)

    @mcp.tool(description="Todos los equipos de un club (A/B, categorías) y, con results, sus resultados.")
    def club_overview(sport: SportArg, name: Name, results: Annotated[bool, Field(description="Incluir resultados de cada equipo")] = False,
                      category: Category | None = None, limit: Limit = API_DEFAULT_LIMIT) -> dict[str, Any]:
        return get("/v2/clubs/results" if results else "/v2/clubs/search", "clubes", sport=sport, name=name, category=category, limit=limit)

    @mcp.tool(description="Máximos goleadores de las ligas (tablas oficiales), filtrables por categoría (páginas de 10, count = total).")
    def scorers_ranking(sport: SportArg, category: Category | None = None, gender: Gender | None = None,
                        limit: PageLimit = PAGE_SIZE, offset: Offset = 0) -> dict[str, Any]:
        return get("/v2/rankings/scorers", "goleadores", sport=sport, competition_kind="league", category=category, gender=gender, limit=limit,
                   offset=offset, compact=True)

    @mcp.tool(description="Ranking de equipos en las ligas (sin copas): goles a favor/en contra, victorias, puntos por partido (páginas de 10, count = total).")
    def teams_ranking(sport: SportArg, metric: Metric, category: Category | None = None, gender: Gender | None = None,
                      limit: PageLimit = PAGE_SIZE, offset: Offset = 0) -> dict[str, Any]:
        return get("/v2/rankings/teams", "ranking", sport=sport, metric=metric, category=category, gender=gender, min_played=1, limit=limit,
                   offset=offset, compact=True, competition_kind="league")
