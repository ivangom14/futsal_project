"""Fase 24, Bloque 1: tipo/categoría/alias de competiciones y resolución antes de preguntar. Catálogo = el real del inventario (14
competiciones, temporada 22), agente real con LLM guionizado y MCP simulado; sin red ni cuota."""

import asyncio
import json
from typing import Any

import pytest

from futsal.agent.agent import Agent
from futsal.agent.llm import LLMResponse, Message, ToolCall
from futsal.agent.memory import Turn
from futsal.config.competition_aliases import load_aliases
from futsal.domain.competitions import (
    Competition,
    candidates,
    classify,
    extract_hints,
    fold,
    merge_hints,
    scope_resolved,
)

CFG = load_aliases()
REAL = {  # nombre oficial -> grupos (inventory.json de la temporada 22)
    "TERCERA DIVISIÓN FS": ["Grupo 3", "Grupo 4"],
    "COPA SENIOR I - fase final- FS": ["Eliminatorias"], "COPA SENIOR I - fase previa- FS": ["Eliminatorias"],
    "PRIMERA DIVISION AUTONOMICA AFICIONADO FS": ["Grupo 1", "Grupo 2"], "PREFERENTE AFICIONADO FS": ["Grupo 1", "Grupo 2"],
    "COPA SENIOR II - fase final- FS": ["Eliminatorias"], "COPA SENIOR II - fase previa- FS": ["Eliminatorias"],
    "PRIMERA AFICIONADO FS": ["Grupo 1", "Grupo 2", "Grupo 3", "Grupo 4"],
    "TROFEO COMUNIDAD DIVISION HONOR JUVENIL FS": ["Eliminatorias"], "PRIMERA DIVISION AUTONOMICA JUVENIL FS": ["Grupo Unico"],
    "PREFERENTE JUVENIL FS": ["Grupo 1", "Grupo 2"], "COPA JUVENIL - fase final- FS": ["Eliminatorias"],
    "COPA JUVENIL - fase previa- FS": ["Eliminatorias"], "PRIMERA JUVENIL FS": ["Grupo 1", "Grupo 2", "Grupo 3", "Grupo 4", "Grupo 5"],
}
COMPS = [Competition(i + 1, n, tuple((100 * (i + 1) + j, g) for j, g in enumerate(gs, 1))) for i, (n, gs) in enumerate(REAL.items())]
ID = {c.name: c.id for c in COMPS}


def names(text: str, prev: str | None = None) -> list[str]:
    hints = extract_hints(text, CFG, COMPS)
    if prev:
        hints = merge_hints(extract_hints(prev, CFG, COMPS), hints, CFG)
    return [c.name for c in candidates(COMPS, hints, CFG)]


# --- Taxonomía ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,kind,age", [
    ("PRIMERA DIVISION AUTONOMICA AFICIONADO FS", "league", "senior"), ("PRIMERA AFICIONADO FS", "league", "senior"),
    ("PRIMERA JUVENIL FS", "league", "juvenil"), ("PRIMERA DIVISION AUTONOMICA JUVENIL FS", "league", "juvenil"),
    ("COPA SENIOR I - fase previa- FS", "cup", "senior"), ("COPA JUVENIL - fase final- FS", "cup", "juvenil"),
    ("TROFEO COMUNIDAD DIVISION HONOR JUVENIL FS", "tournament", "juvenil"), ("TERCERA DIVISIÓN FS", "league", "senior")])
def test_classification_of_the_real_catalog(name: str, kind: str, age: str) -> None:
    assert classify(name, CFG) == (kind, age)


def test_every_alias_target_exists_in_the_real_catalog() -> None:
    official = {fold(n) for n in REAL}
    targets = {fold(n) for e in CFG["aliases"] for n in e["names"]}
    assert targets <= official, targets - official


def test_alias_to_a_missing_competition_is_dropped_and_never_invents_ids() -> None:
    without = [c for c in COMPS if c.name != "PRIMERA JUVENIL FS"]
    hints = extract_hints("¿cómo va el regional juvenil?", CFG, without)
    assert candidates(without, hints, CFG) == [] and not hints.names


# --- Alias (casos 4-6 y variantes) -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("¿Cómo va el regional?", ["PRIMERA AFICIONADO FS"]), ("primera regional", ["PRIMERA AFICIONADO FS"]),
    ("regional senior", ["PRIMERA AFICIONADO FS"]), ("liga regional", ["PRIMERA AFICIONADO FS"]),
    ("regional grupo 2", ["PRIMERA AFICIONADO FS"]),
    ("¿Cómo va el regional juvenil?", ["PRIMERA JUVENIL FS"]), ("primera juvenil", ["PRIMERA JUVENIL FS"]),
    ("Primera Autonómica Senior Grupo 2", ["PRIMERA DIVISION AUTONOMICA AFICIONADO FS"]),
    ("PRIMERA DIVISION AUTONOMICA AFICIONADO FS", ["PRIMERA DIVISION AUTONOMICA AFICIONADO FS"])])
def test_aliases_resolve_to_one_official_competition(text: str, expected: list[str]) -> None:
    assert names(text) == expected


def test_regional_with_group_keeps_its_four_groups_and_the_group_filter() -> None:
    hints = extract_hints("regional grupo 2", CFG, COMPS)
    (comp,) = candidates(COMPS, hints, CFG)
    assert [g for _, g in comp.groups] == ["Grupo 1", "Grupo 2", "Grupo 3", "Grupo 4"] and scope_resolved([comp], hints)
    assert not scope_resolved([comp], extract_hints("regional", CFG, COMPS))  # falta el grupo


@pytest.mark.parametrize("text,expected", [
    ("autonomica", {"PRIMERA DIVISION AUTONOMICA AFICIONADO FS", "PRIMERA DIVISION AUTONOMICA JUVENIL FS"}),
    ("liga de aficionados", {"PRIMERA DIVISION AUTONOMICA AFICIONADO FS", "PREFERENTE AFICIONADO FS", "PRIMERA AFICIONADO FS"}),
    ("preferente", {"PREFERENTE AFICIONADO FS", "PREFERENTE JUVENIL FS"}),
    ("copa senior", {"COPA SENIOR I - fase final- FS", "COPA SENIOR I - fase previa- FS", "COPA SENIOR II - fase final- FS",
                     "COPA SENIOR II - fase previa- FS"}),
    ("copa juvenil", {"COPA JUVENIL - fase final- FS", "COPA JUVENIL - fase previa- FS"})])
def test_ambiguous_aliases_return_all_options_never_one_arbitrary(text: str, expected: set[str]) -> None:
    assert set(names(text)) == expected


def test_kind_and_age_filters() -> None:
    liga = names("liga")
    assert all(classify(n, CFG).kind == "league" for n in liga) and not any("COPA" in n or "TROFEO" in n for n in liga)
    assert all(classify(n, CFG).age == "juvenil" for n in names("liga juvenil")) and "PRIMERA JUVENIL FS" in names("liga juvenil")
    assert names("torneo") == ["TROFEO COMUNIDAD DIVISION HONOR JUVENIL FS"]
    assert not any("JUVENIL" in n for n in names("liga senior"))


def test_a_new_message_overrides_and_the_rest_is_kept() -> None:
    assert not any("COPA" in n for n in names("Senior", prev="¿Cómo ha quedado el DOSA en liga?"))
    assert all(classify(n, CFG).kind == "cup" and classify(n, CFG).age == "senior"
               for n in names("ahora en copa", prev="DOSA en liga senior"))
    assert names("regional juvenil", prev="regional senior") == ["PRIMERA JUVENIL FS"]  # cambio de competición/categoría
    assert names("y en juvenil", prev="regional") and all(classify(n, CFG).age == "juvenil" for n in names("y en juvenil", prev="regional"))


# --- Agente: find_teams filtrado antes de que lo vea el LLM -----------------------------------------------------------------------
DOSA = [  # filas reales de find_teams para C.D. DOSA
    {"id": 11, "name": "C.D. DOSA", "group_id": 211, "group": "Grupo 2", "competition_id": ID["PRIMERA DIVISION AUTONOMICA AFICIONADO FS"],
     "competition": "PRIMERA DIVISION AUTONOMICA AFICIONADO FS"},
    {"id": 12, "name": "C.D. DOSA", "group_id": 301, "group": "Eliminatorias", "competition_id": ID["COPA SENIOR I - fase previa- FS"],
     "competition": "COPA SENIOR I - fase previa- FS"},
    {"id": 13, "name": "C.D. DOSA", "group_id": 1001, "group": "Grupo Unico", "competition_id": ID["PRIMERA DIVISION AUTONOMICA JUVENIL FS"],
     "competition": "PRIMERA DIVISION AUTONOMICA JUVENIL FS"},
    {"id": 14, "name": "C.D. DOSA", "group_id": 1301, "group": "Eliminatorias", "competition_id": ID["COPA JUVENIL - fase final- FS"],
     "competition": "COPA JUVENIL - fase final- FS"},
]


class Mcp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}}
                for n in ("list_competitions", "find_teams", "list_matches", "get_standings")]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        if name == "list_competitions":
            return False, json.dumps({"competitions": [{"id": c.id, "name": c.name, "groups": [{"id": g, "name": n} for g, n in c.groups]}
                                                       for c in COMPS], "count": len(COMPS)})
        self.calls.append((name, arguments))
        if name == "find_teams":
            return False, json.dumps({"teams": DOSA, "count": len(DOSA)})
        return False, json.dumps({"matches": [], "count": 0})


class Script:
    provider, model = "fake", "fake"

    def __init__(self, steps: list[LLMResponse]) -> None:
        self.steps, self.seen = list(steps), []

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.seen.append(list(messages))
        return self.steps.pop(0)


def ask(question: str, history: list[Turn] | None = None, then: list[LLMResponse] | None = None) -> tuple[Script, Any, Mcp]:
    llm = Script([LLMResponse("", [ToolCall("c1", "find_teams", {"name": "DOSA"})]), *(then or [LLMResponse("ok", [])])])
    mcp = Mcp()
    res = asyncio.run(Agent(llm, mcp, 5).run(question, history or []))
    return llm, res, mcp


def teams_seen(llm: Script) -> dict[str, Any]:
    tool_msg = next(m for m in llm.seen[1] if m.role == "tool")
    return json.loads(tool_msg.tool_results[0].content)


def test_case1_liga_offers_only_leagues_without_cups() -> None:
    llm, res, _ = ask("¿Cómo ha quedado el DOSA en liga?")
    data = teams_seen(llm)
    assert [t["competition"] for t in data["teams"]] == ["PRIMERA DIVISION AUTONOMICA AFICIONADO FS", "PRIMERA DIVISION AUTONOMICA JUVENIL FS"]
    assert data["scope_filter"]["excluded"] == 2 and "COPA" not in json.dumps(data["teams"])
    assert res.resolved_scope and res.resolved_scope["resolved"] is False


def test_case2_liga_senior_resolves_directly_and_the_team_id_is_usable() -> None:
    llm, res, mcp = ask("¿Cómo ha quedado el DOSA en liga senior?", then=[
        LLMResponse("", [ToolCall("c2", "list_matches", {"team_id": 11, "status": "finished"})]), LLMResponse("Resultados.", [])])
    assert [t["id"] for t in teams_seen(llm)["teams"]] == [11]
    assert ("list_matches", {"team_id": 11, "status": "finished"}) in mcp.calls and res.answer == "Resultados."


def test_case3_liga_juvenil_resolves_the_juvenile_league() -> None:
    llm, _, _ = ask("¿Cómo va el DOSA en liga juvenil?")
    assert [t["competition"] for t in teams_seen(llm)["teams"]] == ["PRIMERA DIVISION AUTONOMICA JUVENIL FS"]


def test_cup_and_unmatched_filters_are_reported_not_hidden() -> None:
    llm, _, _ = ask("¿Cómo va el DOSA en copa senior?")
    assert [t["competition"] for t in teams_seen(llm)["teams"]] == ["COPA SENIOR I - fase previa- FS"]
    llm, _, _ = ask("¿Cómo va el DOSA en la preferente?")
    data = teams_seen(llm)
    assert data["teams"] == [] and "no figura" in data["note"] and "PRIMERA DIVISION AUTONOMICA AFICIONADO FS" in data["note"]


def test_case4_and_5_aliases_give_the_application_note_with_real_ids() -> None:
    llm, res, _ = ask("¿Cómo va el regional?")
    note = llm.seen[0][-1].text
    assert "Competición identificada: «PRIMERA AFICIONADO FS»" in note and f"competition_id={ID['PRIMERA AFICIONADO FS']}" in note
    assert "«Grupo 4»" in note and res.resolved_scope and res.resolved_scope["competitions"] == ["PRIMERA AFICIONADO FS"]
    llm, _, _ = ask("¿Cómo va el regional juvenil?")
    note = llm.seen[0][-1].text
    assert "«PRIMERA JUVENIL FS»" in note and "«Grupo 5»" in note and "AFICIONADO" not in note.split("Competición identificada")[1]


def test_case6_ambiguous_alias_asks_instead_of_choosing() -> None:
    llm, res, _ = ask("¿Cómo va la autonómica?")
    note = llm.seen[0][-1].text
    assert "Competiciones posibles" in note and "AFICIONADO" in note and "JUVENIL" in note and "Competición identificada" not in note
    assert res.resolved_scope and res.resolved_scope["resolved"] is False and res.resolved_scope["total"] == 2


def test_no_note_and_no_filter_when_the_user_gave_no_scope_hints() -> None:
    llm, res, _ = ask("¿Cómo va el DOSA?")
    assert llm.seen[0][-1].text == "¿Cómo va el DOSA?" and len(teams_seen(llm)["teams"]) == 4 and res.resolved_scope is None


def test_conversation_chain_keeps_team_category_and_type() -> None:
    first = Turn("¿Cómo ha quedado el DOSA en liga?", "El DOSA participa en varias ligas. ¿A qué competición te refieres?")
    llm, res, _ = ask("Senior", [first])  # respuesta corta a la aclaración → se une a la pregunta original
    assert res.resolved_question and res.resolved_question.startswith("¿Cómo ha quedado el DOSA en liga?")
    assert [t["id"] for t in teams_seen(llm)["teams"]] == [11]
    follow = [Turn("¿Cómo ha quedado el DOSA en liga senior?", "Va 3.º."), ]
    llm, res, _ = ask("¿Y el próximo partido?", follow)  # el ámbito se hereda
    assert [t["id"] for t in teams_seen(llm)["teams"]] == [11] and res.resolved_scope and res.resolved_scope["inherited"] is True
    assert "heredados" in llm.seen[0][-1].text


def test_changing_category_in_the_same_conversation_does_not_drag_the_old_filter() -> None:
    follow = [Turn("¿Cómo ha quedado el DOSA en liga senior?", "Va 3.º.")]
    llm, _, _ = ask("¿Y en juvenil?", follow)
    assert [t["id"] for t in teams_seen(llm)["teams"]] == [13]
