"""Fase 3E · intención `groups_list` («¿qué grupos tiene X?»): respuesta determinista con la herramienta existente `list_groups`,
sin LLM, sin aclaración de grupo y sin consultar clasificaciones; igual en fútbol sala y fútbol 11 (MCP simulado, sin red)."""

import asyncio
import json
from typing import Any

import pytest

from futsal.agent.agent import GROUPS_PAGE, Agent, group_sort_key, groups_text
from futsal.agent.intent import classify
from futsal.agent.llm import LLMResponse
from futsal.agent.presentation import build_suggestions, build_visuals

SPORTS = {"football11": {"SEGUNDA AFICIONADO": 22, "TERCERA FEDERACIÓN RFEF": 1, "PREFERENTE AFICIONADO": 4, "PREFERENTE JUVENIL": 8},
          "futsal": {"PREFERENTE AFICIONADO FS": 2, "TERCERA DIVISIÓN FS": 2}}


class NoLLM:
    provider, model = "test", "test"

    def complete(self, system: str, messages: list[Any], tools: list[dict[str, Any]]) -> LLMResponse:
        raise AssertionError("«¿qué grupos tiene X?» no debe llamar al LLM")


class CatalogMcp:
    """Catálogo por deporte: el deporte llega en cada llamada (SportScopedMcp) y solo se ve el del deporte activo."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.comps = {sport: [{"id": 100 * k + j, "name": name, "groups": [{"id": 1000 * (100 * k + j) + g, "name": f"Grupo {g}"}
                                                                           for g in range(n, 0, -1)]}  # al revés: se ordena al responder
                              for j, (name, n) in enumerate(cat.items())]
                      for k, (sport, cat) in enumerate(SPORTS.items(), start=1)}

    async def list_tools(self) -> list[dict[str, Any]]:
        names = ("list_competitions", "list_groups", "find_teams", "list_teams", "list_matches", "get_standings", "get_scorer_table")
        return [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {"sport": {"type": "string"}}}} for n in names]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        self.calls.append((name, dict(arguments)))
        comps = self.comps[arguments["sport"]]
        if name == "list_competitions":
            return False, json.dumps({"competitions": comps, "count": len(comps)})
        if name == "list_groups":
            comp = next(c for c in comps if c["id"] == arguments["competition_id"])
            return False, json.dumps({"groups": comp["groups"], "count": len(comp["groups"])})
        if name == "list_teams":
            return False, json.dumps({"teams": []})
        return True, f"no esperada: {name}"


def ask(question: str, sport: str, state: dict[str, Any] | None = None) -> tuple[Any, CatalogMcp]:
    mcp = CatalogMcp()
    agent = Agent(NoLLM(), mcp, 8)
    kw: dict[str, Any] = {"sport": sport, "conversation_id": "t"}
    if state is not None:
        kw["state"] = state
    return asyncio.run(agent.run(question, **kw)), mcp


@pytest.mark.parametrize("q", ["¿Qué grupos tiene Segunda Aficionado?", "Dime los grupos de Preferente Juvenil", "¿Cuántos grupos hay en Primera Aficionado?",
                               "Muéstrame los grupos de esta competición", "¿Qué grupos tiene Preferente de fútbol sala?", "Muéstrame todos los grupos de X"])
def test_the_five_phrasings_are_groups_list(q: str) -> None:
    assert classify(q).intent == "groups_list" and classify(q).confidence == "high"


@pytest.mark.parametrize("q", ["¿Y quién lidera el grupo 3?", "¿En qué grupo juega el DOSA?", "Clasificación de todos los grupos de Tercera",
                               "¿Cuántos equipos hay en la tercera?", "¿Cómo va el grupo 2?"])
def test_group_questions_that_are_not_a_list_of_groups(q: str) -> None:
    assert classify(q).intent != "groups_list"


def test_long_list_is_paginated_with_the_total_and_no_llm_nor_standings() -> None:
    res, mcp = ask("¿Qué grupos tiene Segunda Aficionado?", "football11")
    assert res.guardrail is None and res.run.llm_calls == []
    used = [n for n, _ in mcp.calls if n not in ("list_competitions", "list_teams")]
    assert used == ["list_groups"] and all(a["sport"] == "football11" for _, a in mcp.calls)
    assert res.answer.startswith("SEGUNDA AFICIONADO tiene 22 grupos.") and "¿De qué grupo" not in res.answer
    assert "Grupo 1, Grupo 2," in res.answer and f"Grupo {GROUPS_PAGE}." in res.answer and "Grupo 13" not in res.answer
    info = res.run.context["groups_list"]
    assert info["total"] == 22 and info["partial"] is True and len(info["groups"]) == GROUPS_PAGE
    visuals = build_visuals(res.run.question, res.run, [], {"teams": {}}, {}, {}, "football11")
    table = next(v for v in visuals if v["type"] == "table")
    assert table["subtitle"] == f"Mostrando 1–{GROUPS_PAGE} de 22" and table["partial"] and len(table["rows"]) == GROUPS_PAGE
    chips = build_suggestions(res.run.question, res.answer, res.run, {}, [], [], visuals)
    assert chips[0] == "Muéstrame todos los grupos de SEGUNDA AFICIONADO" and "Grupo 1 de SEGUNDA AFICIONADO" in chips[1]


def test_all_groups_on_request_and_count_questions() -> None:
    res, _ = ask("Muéstrame todos los grupos de Segunda Aficionado", "football11")
    assert res.run.llm_calls == [] and "Grupo 22" in res.answer and res.run.context["groups_list"]["partial"] is False
    one, _ = ask("¿Cuántos grupos tiene Tercera Federación?", "football11")
    assert one.answer == "TERCERA FEDERACIÓN RFEF tiene 1 grupo: Grupo 1."


def test_same_behaviour_in_futsal_and_never_the_other_sport() -> None:
    res, mcp = ask("¿Qué grupos tiene Tercera División?", "futsal")
    assert res.run.llm_calls == [] and res.answer.startswith("TERCERA DIVISIÓN FS tiene 2 grupos: Grupo 1, Grupo 2.")
    assert {a["sport"] for _, a in mcp.calls} == {"futsal"}


def test_ambiguous_competition_asks_the_competition_not_the_group() -> None:
    from futsal.agent.understanding_eval import SimLLM

    mcp = CatalogMcp()
    res = asyncio.run(Agent(SimLLM(), mcp, 8).run("¿Qué grupos tiene Preferente?", sport="football11", conversation_id="t"))
    assert res.guardrail == "aclaracion_ambito" and "PREFERENTE AFICIONADO" in res.answer and "PREFERENTE JUVENIL" in res.answer
    assert "¿De qué grupo" not in res.answer and not [n for n, _ in mcp.calls if n == "list_groups"]


def test_memory_follow_up_keeps_the_competition_without_asking_again() -> None:
    first, _ = ask("¿Qué grupos tiene Preferente Aficionado?", "football11")
    assert first.state and first.state.get("competitions") == ["PREFERENTE AFICIONADO"] and not first.state.get("groups")
    assert first.state.get("pending") is None


def test_natural_order_and_text() -> None:
    assert sorted(["Grupo 10", "Grupo 2", "Grupo 1", "PRIMERA RONDA"], key=group_sort_key) == ["Grupo 1", "Grupo 2", "Grupo 10", "PRIMERA RONDA"]
    assert groups_text("X", ["Grupo 1", "Grupo 2"], ["Grupo 1", "Grupo 2"]).startswith("X tiene 2 grupos: Grupo 1, Grupo 2.")
