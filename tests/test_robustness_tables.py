"""Fase 17 · robustez de las tablas oficiales en MCP y criterios: errores de API, contratos de parámetros y cifras sin tabla. Sin red."""
import asyncio
from typing import Any

import httpx
import pytest

from futsal.agent.criteria import figures_without_table
from futsal.mcp_server.server import create_server

TABLE_TOOLS = [("get_standings", {"group_id": 999}), ("get_scorer_table", {"group_id": 999})]


def _call(name: str, args: dict[str, Any], handler: Any) -> tuple[bool, str]:
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    server = create_server(httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler)))

    async def go() -> tuple[bool, str]:
        async with connect(server._mcp_server) as session:
            res = await session.call_tool(name, args)
            return res.isError, res.content[0].text  # type: ignore[union-attr]

    return asyncio.run(go())


@pytest.mark.parametrize("name,args", TABLE_TOOLS)
@pytest.mark.parametrize("failure,expected", [
    (lambda r: httpx.Response(404, json={"detail": "grupo 999 no encontrado"}), "error_no_encontrado"),
    (lambda r: httpx.Response(503, json={"detail": "caída"}), "error_dependencia"),
    (lambda r: httpx.Response(500, text="boom"), "error_dependencia"),
    (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")), "error_timeout"),
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")), "error_dependencia"),
])
def test_table_tools_turn_api_failures_into_controlled_errors_never_into_data(name: str, args: dict[str, Any],
                                                                             failure: Any, expected: str) -> None:
    is_error, text = _call(name, args, failure)
    assert is_error and expected in text and "{" not in text  # texto de error, no un JSON con filas


def test_table_tools_keep_their_public_contract() -> None:
    tools = {t.name: t for t in asyncio.run(create_server().list_tools())}
    assert set(tools["get_standings"].inputSchema["properties"]) == {"group_id", "round"}
    assert set(tools["get_scorer_table"].inputSchema["properties"]) == {"group_id", "competition_id", "name", "limit"}
    assert tools["get_standings"].inputSchema["required"] == ["group_id"]


def test_table_tools_forward_exactly_the_requested_parameters() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"status": "ok"})

    _call("get_standings", {"group_id": 7, "round": 3}, handler)
    _call("get_standings", {"group_id": 7}, handler)
    _call("get_scorer_table", {"group_id": 7, "name": "Perez", "limit": 5}, handler)
    _call("get_scorer_table", {"competition_id": 4, "limit": 2}, handler)
    assert [(r.url.path, dict(r.url.params)) for r in seen] == [
        ("/groups/7/standings", {"round": "3"}), ("/groups/7/standings", {}),
        ("/groups/7/scorer-table", {"name": "Perez", "limit": "5"}), ("/competitions/4/scorer-table", {"limit": "2"})]


def test_scorer_table_requires_a_group_or_a_competition_and_rejects_bad_rounds() -> None:
    is_error, text = _call("get_scorer_table", {}, lambda r: httpx.Response(200, json={}))
    assert is_error and "error_validacion" in text
    is_error, _ = _call("get_standings", {"group_id": 1, "round": 0}, lambda r: httpx.Response(200, json={}))
    assert is_error


def test_figures_without_a_table_flag_invented_numbers_but_not_those_the_tool_reported() -> None:
    no_table = [{"status": "round_not_ingested", "requested_round": 26, "available_rounds": [5], "message": "La jornada 26 no está ingerida"}]
    assert figures_without_table("Lidera con 9 puntos.", no_table, "¿Quién lidera?") == ["9"]
    assert figures_without_table("Solo está ingerida la jornada 5; la 26 no consta.", no_table, "¿Y tras la jornada 26?") == []
    assert figures_without_table("Lidera con 9 puntos.", [{"standings": [{"team": "A", "points": 9}]}]) == []   # con tabla no se aplica
    assert figures_without_table("Lidera con 9 puntos.", [{"standings": []}]) == ["9"]                          # tabla vacía no es evidencia
    assert figures_without_table("No consta.", []) == []
