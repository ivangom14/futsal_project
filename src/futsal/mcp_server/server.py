"""Servidor MCP de solo lectura: capa fina de herramientas sobre la API REST (FastAPI)."""

import sys
from typing import Annotated, Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from futsal.config.settings import api_base_url

TIMEOUT_SECONDS = 10.0

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
    ) -> dict[str, Any]:
        """Lista los partidos de un grupo con equipos, fecha, estado y marcador.
        Filtra por jornada y/o estado (p. ej. `finished` para resultados ya jugados)."""
        return _listing(
            "matches",
            _get(http, f"/groups/{group_id}/matches", f"grupo {group_id}",
                 round_id=round_id, status=status),
        )

    @mcp.tool()
    def get_match(
        match_id: Annotated[int, Field(description="ID interno de partido")],
    ) -> dict[str, Any]:
        """Devuelve el detalle de un partido: equipos, marcador, estado, pista, zona horaria
        y observaciones disponibles."""
        return _get(http, f"/matches/{match_id}", f"partido {match_id}")

    return mcp


def main() -> None:
    print(f"futsal MCP (stdio) → API {api_base_url()}", file=sys.stderr)
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
