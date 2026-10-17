"""Tests del servidor MCP con la API simulada (httpx.MockTransport)."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from futsal.mcp_server.server import create_server

TOOLS = {"list_competitions", "list_groups", "list_rounds", "list_teams", "list_matches",
         "get_match", "get_match_report", "list_match_events", "top_scorers", "find_players", "find_teams"}
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


def test_list_matches_forwards_team_id() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=LISTING)

    err, _ = _run("list_matches", {"group_id": 1, "team_id": 11, "status": "finished"}, handler)
    assert not err and seen == {"team_id": "11", "status": "finished"}
    tools = asyncio.run(create_server().list_tools())
    props = next(t for t in tools if t.name == "list_matches").inputSchema["properties"]
    assert "team_id" in props


def _echo(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"path": request.url.path, "params": dict(request.url.params)})


def test_report_tools_forward_filters_to_read_only_api_routes() -> None:
    _, text = _run("get_match_report", {"match_id": 7, "sections": ["officials", "cards"]}, _echo)
    assert json.loads(text) == {"path": "/matches/7/report", "params": {"sections": "officials,cards"}}
    _, text = _run("top_scorers", {"group_id": 1, "round_id": 3, "min_goals": 2, "against_team_id": 9}, _echo)
    got = json.loads(text)
    assert got["path"] == "/groups/1/scorers"
    assert got["params"] == {"round_id": "3", "min_goals": "2", "against_team_id": "9", "limit": "20"}
    _, text = _run("list_match_events", {"group_id": 1, "event_type": "card", "player_id": 4}, _echo)
    assert json.loads(text)["params"] == {"event_type": "card", "player_id": "4", "limit": "40"}
    _, text = _run("find_players", {"group_id": 1, "name": "perez"}, _echo)
    assert json.loads(text) == {"path": "/groups/1/players", "params": {"name": "perez"}}
    seen: list[dict[str, str]] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=LISTING)

    _run("list_matches", {"group_id": 1, "status": "finished", "has_report": False}, _capture)
    assert seen == [{"status": "finished", "has_report": "false"}]


def test_report_tools_reject_invalid_parameters_and_map_missing_match() -> None:
    assert _run("list_match_events", {"group_id": 1, "event_type": "penalty"}, _echo)[0] is True
    assert _run("top_scorers", {"group_id": 1, "limit": 999}, _echo)[0] is True
    err, text = _run("get_match_report", {"match_id": 999})
    assert err and "error_no_encontrado" in text


def test_list_matches_forwards_limit_and_exposes_shown() -> None:
    seen: list[httpx.URL] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url)
        return httpx.Response(200, json={"items": [{"id": 1}], "count": 163, "shown": 1})

    is_error, text = _run("list_matches", {"group_id": 1, "status": "scheduled", "limit": 1}, handler)
    assert dict(seen[0].params) == {"status": "scheduled", "limit": "1"}
    assert not is_error and json.loads(text) == {"matches": [{"id": 1}], "count": 163, "shown": 1}
    plain = _run("list_matches", {"group_id": 1}, lambda r: httpx.Response(200, json=LISTING))
    assert "shown" not in json.loads(plain[1])


def test_list_matches_limit_is_enforced_even_if_the_api_ignores_it() -> None:
    old_api = lambda req: httpx.Response(200, json={"items": [{"id": i} for i in range(19)], "count": 19})  # noqa: E731
    is_error, text = _run("list_matches", {"group_id": 1, "status": "finished", "limit": 1}, old_api)
    assert not is_error and json.loads(text) == {"matches": [{"id": 0}], "count": 19, "shown": 1}


# --- Fase 14: ámbito de consulta (grupo / equipo entre grupos) ---

def test_find_teams_forwards_name_and_keeps_group_scope() -> None:
    seen: list[httpx.URL] = []
    teams = {"items": [{"id": 1, "name": "FS A", "group_id": 3, "group": "Grupo 1", "competition_id": 2,
                        "competition": "C"}, {"id": 9, "name": "FS A", "group_id": 4, "group": "Grupo 2",
                                              "competition_id": 2, "competition": "C"}], "count": 2}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url)
        return httpx.Response(200, json=teams)

    is_error, text = _run("find_teams", {"name": "FS A"}, handler)
    data = json.loads(text)
    assert not is_error and seen[0].path == "/teams" and seen[0].params["name"] == "FS A"
    assert [t["group_id"] for t in data["teams"]] == [3, 4] and data["count"] == 2  # mismo nombre, dos grupos


def test_find_teams_rejects_short_name_and_reports_api_errors() -> None:
    is_error, _ = _run("find_teams", {"name": "a"})
    assert is_error
    is_error, text = _run("find_teams", {"name": "ab"}, lambda r: httpx.Response(503))
    assert is_error and "error_dependencia" in text


def test_list_matches_uses_the_requested_group_not_a_default() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        return httpx.Response(200, json=LISTING)

    _run("list_matches", {"group_id": 26}, handler)
    _run("list_matches", {"group_id": 4}, handler)
    assert seen == ["/groups/26/matches", "/groups/4/matches"]
