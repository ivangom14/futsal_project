"""Tests del servidor MCP con la API simulada (httpx.MockTransport)."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from futsal.mcp_server.server import create_server

TOOLS = {"list_competitions", "list_groups", "list_rounds", "list_teams", "list_matches",
         "get_match"}
LISTING = {"items": [{"id": 1}], "count": 1}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/matches/5":
        return httpx.Response(200, json={"id": 5, "observations": ["x"]})
    if path.startswith("/matches/") or path == "/groups/999/teams":
        return httpx.Response(404, json={"detail": "no encontrado"})
    if path == "/groups/1/matches" and request.url.params.get("status") == "bad":
        return httpx.Response(400, json={"detail": "status inválido"})
    return httpx.Response(200, json=LISTING)


def _run(name: str, args: dict[str, Any], handler: Any = _handler) -> tuple[bool, str]:
    """Devuelve (es_error, texto) como lo vería un cliente MCP."""
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    client = httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))
    server = create_server(client)

    async def go() -> tuple[bool, str]:
        async with connect(server._mcp_server) as session:
            res = await session.call_tool(name, args)
            return res.isError, res.content[0].text  # type: ignore[union-attr]

    return asyncio.run(go())


def test_tools_registered_with_descriptions() -> None:
    tools = asyncio.run(create_server().list_tools())
    assert {t.name for t in tools} == TOOLS
    assert all(t.description and t.inputSchema for t in tools)


@pytest.mark.parametrize("name,args,key", [
    ("list_competitions", {}, "competitions"),
    ("list_groups", {"competition_id": 1}, "groups"),
    ("list_rounds", {"group_id": 1}, "rounds"),
    ("list_teams", {"group_id": 1}, "teams"),
    ("list_matches", {"group_id": 1, "status": "finished"}, "matches"),
])
def test_listing_tools(name: str, args: dict[str, Any], key: str) -> None:
    is_error, text = _run(name, args)
    assert not is_error
    assert json.loads(text) == {key: [{"id": 1}], "count": 1}


def test_get_match() -> None:
    is_error, text = _run("get_match", {"match_id": 5})
    assert not is_error and json.loads(text)["observations"] == ["x"]


def test_list_matches_forwards_filters() -> None:
    seen: list[httpx.URL] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url)
        return httpx.Response(200, json=LISTING)

    _run("list_matches", {"group_id": 1, "round_id": 2, "status": "finished"}, handler)
    assert seen[0].path == "/groups/1/matches"
    assert dict(seen[0].params) == {"round_id": "2", "status": "finished"}


def test_not_found_is_controlled() -> None:
    is_error, text = _run("get_match", {"match_id": 999})
    assert is_error and "error_no_encontrado" in text and "partido 999" in text
    assert "Traceback" not in text


def test_invalid_parameter_type_is_controlled() -> None:
    is_error, text = _run("list_matches", {"group_id": "abc"})
    assert is_error and "group_id" in text


def test_invalid_status_is_controlled() -> None:
    is_error, text = _run("list_matches", {"group_id": 1, "status": "bad"})
    assert is_error and "status" in text


def test_api_400_is_validation_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "round_id inválido"})

    is_error, text = _run("list_matches", {"group_id": 1}, handler)
    assert is_error and "error_validacion" in text


def test_api_down_is_controlled() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    is_error, text = _run("list_competitions", {}, handler)
    assert is_error and "error_dependencia" in text


def test_timeout_is_controlled() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    is_error, text = _run("list_competitions", {}, handler)
    assert is_error and "error_timeout" in text
