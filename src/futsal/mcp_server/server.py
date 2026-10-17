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
    out = {key: data["items"], "count": data["count"]}
    if "shown" in data:  # recorte por `limit`: count sigue siendo el total de coincidencias
        out["shown"] = data["shown"]
    return out


def create_server(client: httpx.Client | None = None) -> FastMCP:
    """Crea el servidor; `client` permite inyectar un cliente HTTP (tests)."""
    http = client or httpx.Client(base_url=api_base_url(), timeout=TIMEOUT_SECONDS)
    mcp = FastMCP("futsal")

    @mcp.tool()
    def list_competitions(
        season_id: Annotated[int | None, Field(description="ID de temporada")] = None,
    ) -> dict[str, Any]:
        """Competiciones con sus grupos (id y nombre); el `id` del grupo es el group_id del resto de tools."""
        return _listing("competitions", _get(http, "/competitions", "temporada", season_id=season_id))

    @mcp.tool()
    def list_groups(
        competition_id: Annotated[int, Field(description="ID de competición")],
    ) -> dict[str, Any]:
        """Grupos de una competición; su `id` es el group_id del resto de tools."""
        return _listing(
            "groups",
            _get(http, f"/competitions/{competition_id}/groups", f"competición {competition_id}"),
        )

    @mcp.tool()
    def find_teams(
        name: Annotated[str, Field(min_length=2, max_length=80, description="Nombre o parte del nombre")],
    ) -> dict[str, Any]:
        """Busca equipos por nombre en todos los grupos (team_id, group_id, grupo y competición). Si salen
        varios grupos y la pregunta no lo precisa, pregunta cuál en vez de elegir."""
        return _listing("teams", _get(http, "/teams", "equipos", name=name))

    @mcp.tool()
    def list_rounds(
        group_id: Annotated[int, Field(description="ID de grupo")],
    ) -> dict[str, Any]:
        """Jornadas de un grupo por número; su `id` es el round_id de list_matches."""
        return _listing("rounds", _get(http, f"/groups/{group_id}/rounds", f"grupo {group_id}"))

    @mcp.tool()
    def list_teams(
        group_id: Annotated[int, Field(description="ID de grupo")],
    ) -> dict[str, Any]:
        """Equipos de un grupo por nombre."""
        return _listing("teams", _get(http, f"/groups/{group_id}/teams", f"grupo {group_id}"))

    @mcp.tool()
    def list_matches(
        group_id: Annotated[int, Field(description="ID de grupo")],
        round_id: Annotated[int | None, Field(description="ID de jornada, no número")] = None,
        status: Annotated[MatchStatus | None, Field(description="Estado")] = None,
        team_id: Annotated[int | None, Field(description="Solo partidos de este equipo")] = None,
        has_report: Annotated[bool | None, Field(description="Con/sin acta")] = None,
        limit: Annotated[int | None, Field(ge=1, le=200, description=(
            "Solo las N primeras filas; count sigue siendo el total (para contar: limit=1)"))] = None,
    ) -> dict[str, Any]:
        """Partidos de un grupo: equipos, fecha, estado, marcador y has_report (acta disponible). Filtros
        opcionales; count = total de coincidencias (shown = filas devueltas si hay limit)."""
        data = _get(http, f"/groups/{group_id}/matches", f"grupo {group_id}",
                    round_id=round_id, status=status, team_id=team_id, has_report=has_report, limit=limit)
        if limit is not None and len(data["items"]) > limit:  # API anterior a `limit` (sin reiniciar): se recorta aquí
            data = {"items": data["items"][:limit], "count": len(data["items"]), "shown": limit}
        return _listing("matches", data)

    @mcp.tool()
    def get_match(
        match_id: Annotated[int, Field(description="ID de partido")],
    ) -> dict[str, Any]:
        """Detalle de un partido: equipos, marcador, estado, pista y zona horaria."""
        return _get(http, f"/matches/{match_id}", f"partido {match_id}")

    @mcp.tool()
    def get_match_report(
        match_id: Annotated[int, Field(description="ID de partido")],
        sections: Annotated[list[ReportSection] | None, Field(
            description="Por defecto officials y goals")] = None,
    ) -> dict[str, Any]:
        """Acta: árbitros, goles, tarjetas, alineaciones y consistency (marcador vs acta). Sin acta:
        report_available=false y motivo; not_in_source: lo que no consta."""
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
        """Eventos de actas (goles, propia meta, tarjetas): partido, equipo, rival, jugador, minuto y coverage
        (si es parcial, faltar no prueba nada)."""
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
        """Goles por jugador de más a menos (sin propia meta), mismos filtros, con coverage."""
        return _get(http, f"/groups/{group_id}/scorers", f"grupo {group_id}", team_id=team_id,
                    against_team_id=against_team_id, round_id=round_id, match_id=match_id,
                    player_id=player_id, min_goals=min_goals, limit=limit)

    @mcp.tool()
    def find_players(
        group_id: int,
        name: Annotated[str, Field(min_length=2, max_length=80, description="Nombre o apellido")],
        team_id: int | None = None,
    ) -> dict[str, Any]:
        """Jugadores de las actas por nombre (devuelve player_id). Sin resultados: no consta."""
        return _get(http, f"/groups/{group_id}/players", f"grupo {group_id}", name=name, team_id=team_id)

    return mcp


def main() -> None:
    print(f"futsal MCP (stdio) → API {api_base_url()}", file=sys.stderr)
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
