"""Servidor MCP de solo lectura: capa fina de herramientas sobre la API REST (FastAPI)."""

import sys
from typing import Annotated, Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from futsal.config.settings import api_base_url

TIMEOUT_SECONDS = 10.0

EventType = Literal["goal", "own_goal", "card"]
ReportSection = Literal["officials", "goals", "cards", "players", "staff"]
MatchStatus = Literal["scheduled", "finished", "postponed", "suspended", "cancelled", "unknown"]


class ToolError(Exception):
    """Error controlado devuelto al cliente MCP (sin trazas)."""


def _get(client: httpx.Client, path: str, resource: str, **params: Any) -> dict[str, Any]:
    clean = {k: v for k, v in params.items() if v is not None}
    try:
        resp = client.get(path, params=clean)
    except httpx.TimeoutException as exc:
        raise ToolError(f"error_timeout: la API no respondió en {TIMEOUT_SECONDS:.0f}s") from exc
    except httpx.HTTPError as exc:
        raise ToolError("error_dependencia: API no disponible") from exc
    if resp.status_code == 404:
        raise ToolError(f"error_no_encontrado: {resource} no existe")
    if resp.status_code in (400, 422):
        raise ToolError(f"error_validacion: parámetros inválidos ({_detail(resp)})")
    if resp.status_code >= 500:
        raise ToolError(f"error_dependencia: la API devolvió {resp.status_code}")
    if resp.status_code >= 400:
        raise ToolError(f"error_api: la API devolvió {resp.status_code}")
    data = resp.json()
    if not isinstance(data, dict):
        raise ToolError("error_dependencia: respuesta inesperada de la API")
    return data


def _detail(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("detail", ""))[:200]
    except ValueError:
        return ""


def _listing(key: str, data: dict[str, Any]) -> dict[str, Any]:
    return {key: data["items"], "count": data["count"]}


def create_server(client: httpx.Client | None = None) -> FastMCP:
    """Crea el servidor; `client` permite inyectar un cliente HTTP (tests)."""
    http = client or httpx.Client(base_url=api_base_url(), timeout=TIMEOUT_SECONDS)
    mcp = FastMCP("futsal")

    @mcp.tool()
    def list_competitions(
        season_id: Annotated[int | None, Field(description="ID interno de temporada (opcional)")]
        = None,
    ) -> dict[str, Any]:
        """Lista las competiciones de fútbol sala disponibles, opcionalmente de una temporada.
        Punto de partida: devuelve los `id` de competición para `list_groups`."""
        return _listing("competitions", _get(http, "/competitions", "temporada", season_id=season_id))

    @mcp.tool()
    def list_groups(
        competition_id: Annotated[int, Field(description="ID interno de competición")],
    ) -> dict[str, Any]:
        """Lista los grupos de una competición. Devuelve los `id` de grupo usados por
        `list_rounds`, `list_teams` y `list_matches`."""
        return _listing(
            "groups",
            _get(http, f"/competitions/{competition_id}/groups", f"competición {competition_id}"),
        )

    @mcp.tool()
    def list_rounds(
        group_id: Annotated[int, Field(description="ID interno de grupo")],
    ) -> dict[str, Any]:
        """Lista las jornadas de un grupo, ordenadas por número. Devuelve los `id` de jornada
        usables como filtro `round_id` en `list_matches`."""
        return _listing("rounds", _get(http, f"/groups/{group_id}/rounds", f"grupo {group_id}"))

    @mcp.tool()
    def list_teams(
        group_id: Annotated[int, Field(description="ID interno de grupo")],
    ) -> dict[str, Any]:
        """Lista los equipos de un grupo, ordenados por nombre."""
        return _listing("teams", _get(http, f"/groups/{group_id}/teams", f"grupo {group_id}"))

    @mcp.tool()
    def list_matches(
        group_id: Annotated[int, Field(description="ID interno de grupo")],
        round_id: Annotated[int | None, Field(description="ID interno de jornada (no su número);"
                                              " opcional")] = None,
        status: Annotated[MatchStatus | None, Field(description="Filtro de estado (opcional)")]
        = None,
        team_id: Annotated[int | None, Field(description="ID interno de equipo (de list_teams); "
                                             "devuelve solo sus partidos; opcional")] = None,
        has_report: Annotated[bool | None, Field(description="Solo con/sin acta; opcional")] = None,
    ) -> dict[str, Any]:
        """Lista los partidos de un grupo con equipos, fecha, estado, marcador y has_report (acta
        disponible). Filtra por jornada, estado (p. ej. `finished` para resultados ya jugados), equipo
        y/o acta."""
        return _listing(
            "matches",
            _get(http, f"/groups/{group_id}/matches", f"grupo {group_id}",
                 round_id=round_id, status=status, team_id=team_id, has_report=has_report),
        )

    @mcp.tool()
    def get_match(
        match_id: Annotated[int, Field(description="ID interno de partido")],
    ) -> dict[str, Any]:
        """Devuelve el detalle de un partido: equipos, marcador, estado, pista, zona horaria
        y observaciones disponibles."""
        return _get(http, f"/matches/{match_id}", f"partido {match_id}")

    @mcp.tool()
    def get_match_report(
        match_id: Annotated[int, Field(description="ID de partido")],
        sections: Annotated[list[ReportSection] | None, Field(
            description="Por defecto officials y goals")] = None,
    ) -> dict[str, Any]:
        """Acta de un partido: árbitros, goles (jugador, minuto), tarjetas, alineaciones y consistency
        (marcador vs acta). Sin acta: report_available=false y el motivo. not_in_source: lo que no consta."""
        return _get(http, f"/matches/{match_id}/report", f"partido {match_id}",
                    sections=",".join(sections) if sections else None)

    @mcp.tool()
    def list_match_events(
        group_id: int,
        team_id: Annotated[int | None, Field(description="Jugadores de este equipo")] = None,
        against_team_id: Annotated[int | None, Field(description="Goles contra este equipo")] = None,
        round_id: Annotated[int | None, Field(description="ID de jornada, no número")] = None,
        match_id: int | None = None,
        player_id: int | None = None,
        event_type: Annotated[EventType | None, Field(description="Def.: goal y own_goal")] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 40,
    ) -> dict[str, Any]:
        """Eventos de actas (goles, propia meta, tarjetas) con partido, equipo, rival, jugador y minuto.
        coverage indica cuántos partidos finalizados tienen acta: si es parcial, faltar no prueba nada."""
        return _get(http, f"/groups/{group_id}/match-events", f"grupo {group_id}", team_id=team_id,
                    against_team_id=against_team_id, round_id=round_id, match_id=match_id,
                    player_id=player_id, event_type=event_type, limit=limit)

    @mcp.tool()
    def top_scorers(
        group_id: int,
        team_id: int | None = None,
        against_team_id: int | None = None,
        round_id: Annotated[int | None, Field(description="ID de jornada, no número")] = None,
        match_id: int | None = None,
        player_id: int | None = None,
        min_goals: Annotated[int, Field(ge=1, le=100)] = 1,
        limit: Annotated[int, Field(ge=1, le=200)] = 20,
    ) -> dict[str, Any]:
        """Goles por jugador (agrupados en la base de datos, de más a menos) con los mismos filtros.
        No cuenta propia meta. Incluye coverage."""
        return _get(http, f"/groups/{group_id}/scorers", f"grupo {group_id}", team_id=team_id,
                    against_team_id=against_team_id, round_id=round_id, match_id=match_id,
                    player_id=player_id, min_goals=min_goals, limit=limit)

    @mcp.tool()
    def find_players(
        group_id: int,
        name: Annotated[str, Field(min_length=2, max_length=80, description="Nombre o apellido")],
        team_id: int | None = None,
    ) -> dict[str, Any]:
        """Busca jugadores de las actas por nombre y devuelve su player_id. Sin resultados: no consta."""
        return _get(http, f"/groups/{group_id}/players", f"grupo {group_id}", name=name, team_id=team_id)

    return mcp


def main() -> None:
    print(f"futsal MCP (stdio) → API {api_base_url()}", file=sys.stderr)
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
