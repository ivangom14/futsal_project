"""Fase 1 multideporte (sin BD): dominio de deportes, alcance declarativo de ingesta, MCP con deporte obligatorio y cliente del
agente que lo inyecta sin mostrárselo al LLM."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from futsal.agent.mcp_client import SportScopedMcp, strip_titles
from futsal.domain.sports import SportError, parse_scope, sport_of_game_type
from futsal.ingestion.queue import ingest_scope as sc
from futsal.mcp_server.server import create_server

# Catálogo T22 de fútbol 11 de la RFFM (nombre, CodigoCategoria): metadatos públicos de competiciones, sin datos personales.
F11_CATALOG = [
    ("COPA RFEF FASE AUTONÓMICA", "40050"), ("TERCERA FEDERACIÓN RFEF", "40050"), ("COPA DE AFICIONADOS RFFM 2026/2027", "40053"),
    ("PRIMERA DIVISIÓN AUTONÓMICA AFICIONADO", "40053"), ("PREFERENTE AFICIONADO", "40054"), ("PRIMERA AFICIONADO", "40055"),
    ("SEGUNDA AFICIONADO", "40056"), ("NACIONAL JUVENIL", "40059"), ("PRIMERA DIVISIÓN AUTONÓMICA JUVENIL", "40060"),
    ("PREFERENTE JUVENIL", "40061"), ("PRIMERA JUVENIL", "40062"), ("SEGUNDA JUVENIL", "40063"),
    ("SUPERLIGA CADETE", "13529344"), ("PREFERENTE CADETE", "17"), ("PRIMERA ALEVIN", "26"), ("VETERANOS MASCULINO F11 - MOVEMBER", "14387220"),
    ("TERCERA FEDERACIÓN DE FÚTBOL FEMENINO", "10738461"), ("PREFERENTE FÚTBOL FEMENINO", "40072"),
    ("PRIMERA FEMENINO JUVENIL", "2705380"), ("CAMPEONATO UNIVERSITARIO MASCULINO 1ª FASE F11", "2494595"),
]


def test_sport_scope_is_mandatory_and_explicit() -> None:
    assert parse_scope("futsal") == ("futsal",) and parse_scope("all") == ("futsal", "football11")
    for bad in (None, "", "tenis", "football7"):
        with pytest.raises(SportError):
            parse_scope(bad)
    assert sport_of_game_type("1", {"1": "football11", "3": "futsal"}) == "football11"
    assert sport_of_game_type(None, {"3": "futsal"}) is None


def test_scope_file_declares_the_12_competitions_enabled_and_keeps_all_futsal() -> None:
    # Fase 1: las 12 declaradas y deshabilitadas; Fase 3E: habilitadas en la programación compartida (sin actas)
    scope = sc.load_scope()
    assert scope.sports["futsal"].all_competitions and scope.sport_enabled("futsal") and scope.enabled_game_types() == ["3", "1"]
    f11 = scope.sports["football11"]
    assert f11.game_type == "1" and f11.enabled and len(f11.entries) == 12 and all(e.enabled and not e.reports for e in f11.entries)
    assert scope.allows("futsal", "CUALQUIER LIGA FS", "123")


def test_only_the_12_target_competitions_are_in_scope() -> None:
    scope = sc.load_scope()
    status = {name: scope.status("football11", name, code) for name, code in F11_CATALOG}
    in_scope = [n for n, st in status.items() if st != sc.OUT_OF_SCOPE]
    assert len(in_scope) == 12 and in_scope == [n for n, _ in F11_CATALOG[:12]]
    assert all(st == sc.ENABLED for n, st in status.items() if n in in_scope)  # Fase 3E: las 12, habilitadas
    assert [n for n, c in F11_CATALOG if scope.allows("football11", n, c)] == in_scope  # las otras 31, nunca


def test_scope_matching_needs_category_code_and_ignores_season_label() -> None:
    raw = json.loads(sc.SCOPE_PATH.read_text(encoding="utf-8"))
    raw["sports"]["football11"]["enabled"] = True
    for e in raw["sports"]["football11"]["competitions"]:
        e["enabled"] = True
    scope = sc.parse_scope(raw)
    assert scope.allows("football11", "Copa de Aficionados RFFM 2027/2028", "40053")  # otra temporada, mismo CodigoCategoria
    assert not scope.allows("football11", "PREFERENTE AFICIONADO", "552802")  # código de la homónima de fútbol sala
    assert scope.allows("futsal", "PREFERENTE AFICIONADO FS", "552802")  # fútbol sala: todo (competitions=all)
    assert scope.status("beach", "X", None) == sc.OUT_OF_SCOPE


def test_scope_rejects_ambiguous_configuration() -> None:
    with pytest.raises(ValueError):
        sc.parse_scope({"sports": {"a": {"game_type": "1", "competitions": "all"}, "b": {"game_type": "1", "competitions": "all"}}})
    with pytest.raises(ValueError):
        sc.parse_scope({"sports": {"a": {"game_type": "1", "competitions": "algunas"}}})


# --------------------------------------------------------------------------- MCP


def _mcp_with(calls: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"items": [], "count": 0})

    return create_server(httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api"))


def test_every_mcp_tool_requires_sport_and_forwards_it() -> None:
    calls: list[httpx.Request] = []
    server = _mcp_with(calls)
    tools = asyncio.run(server.list_tools())
    assert tools and all("sport" in (t.inputSchema.get("required") or []) for t in tools)
    asyncio.run(server.call_tool("list_competitions", {"sport": "football11"}))
    asyncio.run(server.call_tool("matches_by_date", {"sport": "futsal", "date_from": "2026-12-05"}))
    assert [r.url.params.get("sport") for r in calls] == ["football11", "futsal"]


class _Inner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.server = _mcp_with([])

    async def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description or "", "input_schema": strip_titles(t.inputSchema)}
                for t in await self.server.list_tools()]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        self.calls.append((name, arguments))
        return False, "{}"


def test_agent_client_hides_sport_from_the_llm_and_injects_the_request_sport() -> None:
    inner = _Inner()
    mcp = SportScopedMcp(inner)
    tools = asyncio.run(mcp.list_tools())
    assert all("sport" not in t["input_schema"].get("properties", {}) for t in tools)
    assert all("sport" not in t["input_schema"].get("required", []) for t in tools)
    comp = next(t for t in tools if t["name"] == "list_competitions")
    assert "required" not in comp["input_schema"]  # mismo esquema que antes de la Fase 1 (sin parámetros obligatorios)
    err, text = asyncio.run(mcp.call_tool("list_competitions", {}))
    assert err and "deporte" in text and not inner.calls  # sin deporte activo no se llama: nunca se supone fútbol sala
    mcp.sport = "futsal"
    asyncio.run(mcp.call_tool("list_teams", {"group_id": 7, "sport": "football11"}))  # el LLM no puede cambiarlo
    assert inner.calls == [("list_teams", {"group_id": 7, "sport": "futsal"})]


def test_llm_facing_tool_schemas_are_unchanged_by_the_sport_parameter() -> None:
    """La huella de las herramientas que ve el LLM (tokens) es la misma que sin el parámetro `sport`."""
    inner = _Inner()
    visible = asyncio.run(SportScopedMcp(inner).list_tools())
    raw = asyncio.run(inner.list_tools())
    for v, r in zip(visible, raw, strict=True):
        before = {k: x for k, x in r["input_schema"]["properties"].items() if k != "sport"}
        assert v["input_schema"]["properties"] == before
        required = [x for x in r["input_schema"].get("required", []) if x != "sport"]
        assert v["input_schema"].get("required", []) == required and ("required" in v["input_schema"]) == bool(required)
