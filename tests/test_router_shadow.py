"""Fase 0: router determinista EN SOMBRA. Sin LLM real, sin red, sin PostgreSQL.

Comprueba tres cosas distintas: (1) decisiones del router, (2) que en sombra no altera el agente real, y (3) que los dos datasets
(el de diseño y el nuevo de validación) se evalúan por separado. NO se exigen aquí los umbrales de aceptación del diseño (95 % / 40 %):
se informan en `router_eval` y en `docs/phase0-instrumentation-shadow-router.md`."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import futsal.agent.agent as agent_module
from futsal.agent.agent import Agent
from futsal.agent.llm import LLMResponse, Message, ToolCall
from futsal.agent.memory import Turn
from futsal.agent.router import INTENTS, TIERS, RouterDecision, resolve_scope, route
from futsal.agent.router_eval import FIXTURES, decide, evaluate_set, load_catalog, load_cases, metrics
from futsal.config.competition_aliases import load_aliases

ALIASES = load_aliases()
FULL = load_catalog("full")
SINGLE = load_catalog("single")


def decision(question: str, catalog: list[Any] = FULL, prev: list[str] | None = None, focus: dict[str, str] | None = None) -> RouterDecision:
    hints, ok, _ = resolve_scope(catalog, ALIASES, prev or [], question)
    return route(question, competitions=catalog, hints=hints, scope_ok=ok, groups_total=sum(len(c.groups) for c in catalog),
                 focus=focus or {}, has_history=bool(prev))


# --- 1. decisiones ----------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("question,intent,tier", [
    ("¿Quién lidera la clasificación de Tercera División FS grupo 3?", "standings", "tier0"),
    ("¿Cuántos jugadores pueden estar en pista por equipo?", "rules", "tier0"),
    ("¿Qué dice el reglamento sobre las cesiones al portero?", "rules", "tier0"),
    ("¿Cuál fue el último resultado del C.D. DOSA?", "matches", "tier0"),
    ("Cuéntame un chiste", "unrecognized", "tier2"),
    ("¿Quién lidera el grupo?", "ambiguous", "tier2"),  # 25 grupos y ningún ámbito
    ("Compara la clasificación de PARQUE NORTE F.S. y C.D. LOPE DE VEGA 'B'", "composite", "tier1"),
    ("¿Quién arbitró el último partido del C.D. DOSA?", "matches", "tier1"),  # acta: no es Tier 0
])
def test_basic_decisions(question: str, intent: str, tier: str) -> None:
    d = decision(question)
    assert (d.intent, d.tier) == (intent, tier) and d.intent in INTENTS and d.tier in TIERS


def test_decision_exposes_everything_the_trace_needs() -> None:
    d = decision("¿Cuántos puntos tiene PARQUE NORTE F.S. en Tercera División FS grupo 3?")
    data = d.to_dict()
    for key in ("intent", "tier", "confidence", "score", "reason", "tier0_eligible", "needs_full", "entities", "signals"):
        assert key in data
    assert data["entities"]["scope"]["resolved"] is True and "PARQUE NORTE F.S." in data["entities"]["names"]
    assert data["entities"]["entity_resolution"] == "pending_find_teams"  # el router NO resuelve equipos (eso es find_teams, E/S)
    assert json.dumps(data)  # serializable tal cual en la traza


def test_tier0_eligibility_and_need_for_the_full_system_are_consistent() -> None:
    for q in ("¿Quién lidera la clasificación de Tercera División FS grupo 3?", "Cuéntame un chiste", "¿Y su último partido?",
              "Compara los últimos cinco partidos del C.D. DOSA y del segundo clasificado"):
        d = decision(q)
        assert d.tier0_eligible == (d.tier == "tier0") and d.needs_full == (d.tier == "tier2")


def test_reference_without_conversation_is_not_tier0_and_with_focus_is_tier1() -> None:
    assert decision("¿Cuál fue su último resultado?").tier == "tier2"
    d = decision("¿Y su último partido?", prev=["¿Cómo va el C.D. DOSA?"], focus={"team": "C.D. DOSA"})
    assert d.tier == "tier1" and d.entities["focus_team"] == "C.D. DOSA"


def test_router_is_deterministic_and_pure() -> None:
    q = "¿Quiénes son los máximos goleadores de Tercera División FS grupo 3?"
    assert decision(q).to_dict() == decision(q).to_dict()
    import futsal.agent.router as r
    assert not any(hasattr(r, name) for name in ("httpx", "requests", "socket", "subprocess", "anthropic"))


def test_resolve_scope_matches_what_the_agent_computes() -> None:
    """La réplica del cálculo de ámbito del agente no se desvía de `Agent._resolve_scope`."""
    comps = [{"id": c.id, "name": c.name, "groups": [{"id": g, "name": n} for g, n in c.groups]} for c in FULL]

    class Mcp:
        async def list_tools(self) -> list[dict[str, Any]]:
            return [{"name": "list_competitions", "description": "", "input_schema": {"type": "object", "properties": {}}}]

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
            return False, json.dumps({"competitions": comps})

    class Llm:
        provider = model = "x"

        def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
            return LLMResponse("ok", [])

    for q, prev in [("Clasificación de la autonómica juvenil", []), ("Goleadores de la liga juvenil", []),
                    ("¿Quién lidera Primera Juvenil grupo 2?", []), ("¿Y el grupo 3?", ["Clasificación de Primera Juvenil"])]:
        agent = Agent(Llm(), Mcp(), 5)
        asyncio.run(agent.run(q, [Turn(p, "ok") for p in prev]))
        hints, ok, _ = resolve_scope(FULL, ALIASES, prev, q)
        assert (agent._hints, agent._scope_ok) == (hints, ok), q


# --- 2. en sombra no altera el agente ---------------------------------------------------------------------------------------------
class RecordingMcp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}}
                for n in ("list_competitions", "get_standings", "search_rules", "find_teams", "list_matches")]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        self.calls.append((name, dict(arguments)))
        if name == "list_competitions":
            return False, json.dumps({"competitions": [{"id": 1, "name": "TERCERA DIVISIÓN FS", "groups": [{"id": 6, "name": "Grupo 3"}]}]})
        return False, json.dumps({"hits": [{"text": "La mitad dura 20 minutos"}], "teams": [], "standings": [{"position": 1}], "count": 0})


class Scripted:
    provider, model = "fake", "fake-model"

    def __init__(self, steps: list[LLMResponse]) -> None:
        self.steps = list(steps)
        self.seen: list[tuple[str, list[tuple[str, str, int, int]], list[dict[str, Any]]]] = []

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.seen.append((system, [(m.role, m.text, len(m.tool_calls), len(m.tool_results)) for m in messages], tools))
        return self.steps.pop(0)


def call(name: str, **args: Any) -> LLMResponse:
    return LLMResponse("", [ToolCall(f"c-{name}", name, args)])


SCENARIOS: dict[str, tuple[str, list[Turn], list[LLMResponse]]] = {
    "tool_flow": ("¿Quién lidera la clasificación de Tercera División FS grupo 3?", [],
                  [call("get_standings", group_id=6), LLMResponse("Lidera el DOSA.", [])]),
    "rules_flow": ("¿Cuánto dura cada mitad del partido?", [], [call("search_rules", query="duración mitad"), LLMResponse("20 minutos.", [])]),
    "offtopic_guardrail": ("Cuéntame un chiste", [], [LLMResponse("Había una vez…", [])]),
    "domain_without_tools_nudge": ("¿Cuántos goles lleva el máximo goleador?", [],
                                   [LLMResponse("Lleva 9 goles.", []), call("get_standings", group_id=6), LLMResponse("No consta.", [])]),
    "follow_up_with_history": ("¿Y su posición?", [Turn("¿Cómo va el C.D. DOSA en Tercera División FS grupo 3?", "Va primero.")],
                               [call("get_standings", group_id=6), LLMResponse("Primero.", [])]),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_shadow_router_never_changes_the_real_flow(name: str) -> None:
    question, history, steps = SCENARIOS[name]
    outcomes = []
    for shadow in (True, False):
        llm, mcp = Scripted([LLMResponse(s.text, list(s.tool_calls)) for s in steps]), RecordingMcp()
        res = asyncio.run(Agent(llm, mcp, 5, router_shadow=shadow).run(question, history))
        outcomes.append({"seen": llm.seen, "mcp": mcp.calls, "answer": res.answer, "guardrail": res.guardrail, "error": res.error,
                         "tool_calls": res.tool_calls, "llm_calls": len(res.run.llm_calls), "trace": res.trace})
    with_router, without = outcomes
    assert with_router == without  # mismos mensajes, system, esquemas, llamadas MCP, respuesta, guardarraíl y traza de texto
    assert with_router["llm_calls"] == len(steps) - len(llm.steps)  # el router no consume ni añade llamadas al LLM


def test_shadow_router_adds_no_llm_or_mcp_calls_and_records_its_decision() -> None:
    question, history, steps = SCENARIOS["tool_flow"]
    llm, mcp = Scripted(steps), RecordingMcp()
    res = asyncio.run(Agent(llm, mcp, 5).run(question, history))
    off_llm, off_mcp = Scripted([LLMResponse(x.text, list(x.tool_calls)) for x in SCENARIOS["tool_flow"][2]]), RecordingMcp()
    asyncio.run(Agent(off_llm, off_mcp, 5, router_shadow=False).run(question, history))
    assert len(llm.seen) == len(off_llm.seen) == 2 and mcp.calls == off_mcp.calls  # ni una llamada más al LLM ni a las herramientas
    assert [c[0] for c in mcp.calls].count("get_standings") == 1
    rec = res.run
    assert rec.router_tier == "tier0" and rec.router_intent == "standings" and rec.router_reason and rec.router_confidence == "high"
    assert rec.exec_tier == "tier2" and rec.arch == "legacy"  # lo que se EJECUTÓ sigue siendo el flujo completo actual


def test_router_failure_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(agent_module, "route", boom)
    question, history, steps = SCENARIOS["tool_flow"]
    res = asyncio.run(Agent(Scripted(steps), RecordingMcp(), 5).run(question, history))
    assert res.answer == "Lidera el DOSA." and res.error is None and res.run.router == {"error": "RuntimeError"}
    assert res.run.router_tier is None


def test_kill_switch_disables_the_router(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_ROUTER", "off")
    question, history, steps = SCENARIOS["tool_flow"]
    res = asyncio.run(Agent(Scripted(steps), RecordingMcp(), 5).run(question, history))
    assert res.run.router is None and res.run.router_tier is None and res.answer == "Lidera el DOSA."


# --- 3. datasets y evaluación independiente ----------------------------------------------------------------------------------------
def test_datasets_are_well_formed_and_disjoint() -> None:
    design, validation = load_cases("design_set"), load_cases("validation")
    for doc in (design, validation):
        ids = [c["id"] for c in doc["cases"]]
        assert len(ids) == len(set(ids))
        for c in doc["cases"]:
            if c["evaluable"]:
                assert c["expected_intent"] in INTENTS and c["expected_tier"] in TIERS, c["id"]
    assert sum(c["evaluable"] for c in validation["cases"]) >= 60  # mínimo pedido
    norm = lambda s: " ".join(s.lower().split())  # noqa: E731
    assert not {norm(c["question"]) for c in design["cases"] if c["question"]} & {norm(c["question"]) for c in validation["cases"]}


def test_validation_set_covers_the_requested_kinds_of_question() -> None:
    cats = {c["category"] for c in load_cases("validation")["cases"]}
    assert {"clasificacion", "partidos", "goleadores", "reglamento", "equipo_ambiguo", "ambito", "erratas", "compuestas", "seguimiento", "rechazo"} <= cats
    unverifiable = [c for c in load_cases("validation")["cases"] if not c["evaluable"]]
    assert unverifiable and all("find_teams" in c["note"] for c in unverifiable)  # lo no verificable queda como NO evaluable


def test_each_set_is_evaluated_separately_and_deterministically() -> None:
    a, b = evaluate_set("design_set"), evaluate_set("validation")
    assert a["set"] != b["set"] and a["metrics"]["total"] == 72 and b["metrics"]["total"] >= 100
    assert evaluate_set("validation")["metrics"] == b["metrics"]
    for m in (a["metrics"], b["metrics"]):
        assert m["unsafe_tier0_for_tier2"] == 0  # jamás se propone Tier 0 para lo que debía ir al flujo completo
        assert 0 <= m["tier0"]["coverage_predicted"] <= 1 and m["high_confidence"]["n"] > 0


def test_design_set_is_not_independent_evidence_but_must_not_regress() -> None:
    """Suelo de regresión del router sobre el conjunto con el que se diseñó (NO prueba de calidad: se ajustó mirándolo)."""
    m = evaluate_set("design_set")["metrics"]
    assert m["route_accuracy"] >= 0.88 and m["tier0"]["coverage_predicted"] >= 0.40


def test_metrics_arithmetic_on_a_tiny_synthetic_sample() -> None:
    rows = [
        {"id": "a", "category": "x", "evaluable": True, "exp_intent": "rules", "exp_tier": "tier0", "intent": "rules", "tier": "tier0",
         "confidence": "high", "intent_ok": True, "tier_ok": True},
        {"id": "b", "category": "x", "evaluable": True, "exp_intent": "composite", "exp_tier": "tier1", "intent": "matches", "tier": "tier0",
         "confidence": "high", "intent_ok": False, "tier_ok": False},  # falso positivo de Tier 0
        {"id": "c", "category": "y", "evaluable": True, "exp_intent": "standings", "exp_tier": "tier0", "intent": "standings", "tier": "tier1",
         "confidence": "medium", "intent_ok": True, "tier_ok": False},  # falso negativo
        {"id": "d", "category": "y", "evaluable": False, "question": "?", "note": ""},
    ]
    m = metrics(rows)
    assert (m["total"], m["evaluable"], m["not_evaluable"]) == (4, 3, 1)
    assert m["false_positives"] == 1 and m["false_negatives"] == 1 and m["unsafe_tier0_for_tier2"] == 0
    assert m["high_confidence"] == {"n": 2, "precision_route": 0.5, "precision_tier": 0.5}
    assert m["tier0"]["predicted"] == 2 and m["tier0"]["correct"] == 1 and m["tier0"]["precision"] == 0.5 and m["tier0"]["recall"] == 0.5
    assert m["meets"]["high_confidence_precision>=95%"] is False


def test_decide_uses_the_single_group_catalog_for_the_design_set() -> None:
    case = {"question": "¿Quién lidera la clasificación del grupo?", "prev_questions": [], "focus": {}}
    assert decide(case, "single", ALIASES).tier == "tier0"  # con un solo grupo no falta ámbito
    assert decide(case, "full", ALIASES).tier == "tier2"  # con 25 grupos sí
    assert FIXTURES.joinpath("catalog.json").exists() and len(SINGLE) == 1
    assert Path(FIXTURES).name == "router"
