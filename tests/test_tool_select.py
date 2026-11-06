"""Fase E: selección dinámica de herramientas (AGENT_TOOLS). Recall primero; ante la duda, conjunto completo. Sin LLM real, red ni cuota."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from futsal.agent import datasets
from futsal.agent.agent import Agent, resolve_tools_mode
from futsal.agent.memory import Turn
from futsal.agent.tool_select import ALL_TOOLS, select_tools
from test_prefetch import FakeMcp, Script, call, final

FIX = Path(__file__).parent / "fixtures"
SIZE = {t["name"]: len(json.dumps(t, ensure_ascii=False, separators=(",", ":"))) for t in json.loads((FIX / "prompt_legacy_golden.json").read_text(encoding="utf-8"))["tools_slim"]}


def cases(path: Path) -> list[dict[str, Any]]:
    loaded = datasets.load(path)
    return loaded["cases"] if isinstance(loaded, dict) else loaded


def test_default_is_full_and_unknown_values_fall_back_to_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_TOOLS", raising=False)
    assert resolve_tools_mode() == "full" and resolve_tools_mode("dynamic") == "dynamic" and resolve_tools_mode("raro") == "full"


@pytest.mark.parametrize(("question", "must_have", "must_lack"), [
    ("En TERCERA DIVISIÓN FS (Grupo 3), ¿cuál fue el último resultado del C.D. DOSA?", {"find_teams", "list_matches", "get_match"}, {"get_data_freshness"}),
    ("¿Quién es el máximo goleador del grupo 3?", {"get_scorer_table", "find_players"}, {"get_data_freshness"}),
    ("¿Cuánto dura cada mitad de un partido de fútbol sala?", {"search_rules"}, set()),
    ("¿Qué dice el reglamento sobre el saque de banda?", {"search_rules"}, {"get_standings", "list_matches"}),
])
def test_selection_keeps_what_the_question_needs(question: str, must_have: set[str], must_lack: set[str]) -> None:
    sel = select_tools(question)
    assert sel.tools is not None and must_have <= sel.tools and not must_lack & sel.tools


@pytest.mark.parametrize("question", ["Hola", "¿Y el siguiente partido?", "¿Cuál es la capital de Francia?"])
def test_no_signal_or_context_means_all_tools(question: str) -> None:
    assert select_tools(question, has_context="siguiente" in question).tools is None


def test_search_rules_is_never_dropped_unless_there_is_a_strong_data_anchor() -> None:
    assert "search_rules" in (select_tools("¿Cuánto dura un partido?").tools or ALL_TOOLS)
    assert "search_rules" not in (select_tools("¿Cuál fue el último resultado del C.D. DOSA?").tools or set())


def test_recall_on_the_dev_set_is_complete_and_the_saving_is_real() -> None:
    """Set de DESARROLLO (sobre el que se ajustó el vocabulario). No es evidencia independiente: el Holdout A se midió una vez, sin ajustes posteriores (docs/phase-e-i)."""
    reduced = missed = 0
    saved = 0
    full = sum(SIZE.values())
    for c in cases(FIX / "entities" / "entities_dev.json"):
        sel = select_tools(c["question"], has_context=bool(c.get("prev_questions")) or bool(c.get("followup")))
        if sel.tools is None:
            continue
        reduced += 1
        missed += bool(set(c["needed_tools"]) - sel.tools)
        saved += full - sum(SIZE[t] for t in sel.tools)
    assert reduced > 150 and missed == 0 and saved / reduced / full > 0.3


def run_agent(mode: str, question: str, steps: list[Any]) -> tuple[Any, Script]:
    llm = Script(steps)
    seen: list[list[str]] = []
    real = llm.complete

    def spy(system: str, messages: list[Any], tools: list[dict[str, Any]]) -> Any:
        seen.append([t["name"] for t in tools])
        return real(system, messages, tools)

    llm.complete = spy  # type: ignore[method-assign]
    res = asyncio.run(Agent(llm, FakeMcp(), 6, tools_mode=mode).run(question, []))
    llm.offered = seen  # type: ignore[attr-defined]
    return res, llm


Q = "En TERCERA DIVISIÓN FS (Grupo 3), ¿cuál fue el último resultado del C.D. DOSA?"


def test_dynamic_mode_offers_a_subset_and_full_mode_offers_everything() -> None:
    steps = lambda: [call("find_teams", name="C.D. DOSA"), call("list_matches", team_id=11, group_id=6), final()]
    dyn, ldyn = run_agent("dynamic", Q, steps())
    full, lfull = run_agent("full", Q, steps())
    assert set(ldyn.offered[0]) < set(lfull.offered[0]) and len(lfull.offered[0]) == 6  # FakeMcp expone 6 herramientas
    assert dyn.run.tool_select["mode"] == "dynamic" and full.run.tool_select is None and dyn.answer == full.answer


def spy_agent(steps: list[Any], question: str = Q, history: list[Any] | None = None, **kw: Any) -> tuple[Any, Script]:
    llm = Script(steps)
    offered: list[list[str]] = []
    real = llm.complete

    def spy(system: str, messages: list[Any], tools: list[dict[str, Any]]) -> Any:
        offered.append([t["name"] for t in tools])
        return real(system, messages, tools)

    llm.complete = spy  # type: ignore[method-assign]
    res = asyncio.run(Agent(llm, FakeMcp(), 6, tools_mode="dynamic", **kw).run(question, history or []))
    llm.offered = offered  # type: ignore[attr-defined]
    return res, llm


def test_a_tool_error_does_NOT_widen_the_set() -> None:
    """Un error de datos o de validación no es una señal de herramienta ausente (auditoría I2): no amplía nada."""
    res, llm = spy_agent([call("list_matches", team_id=9999), call("find_teams", name="C.D. DOSA"), final()])
    assert len(llm.offered) == 3 and len({tuple(o) for o in llm.offered}) == 1 and res.run.tool_select["widened"] is False


LIMIT = "No dispongo de una herramienta que cubra eso con los datos que tengo."


def test_calling_a_tool_that_was_not_offered_widens_once_and_keeps_the_context() -> None:
    res, llm = spy_agent([call("list_competitions"), call("find_teams", name="C.D. DOSA"), final("ok")])
    assert "list_competitions" not in llm.offered[0] and "list_competitions" in llm.offered[1]  # FakeMcp tiene 6; el subconjunto inicial deja una fuera
    assert res.run.tool_select["widened"] is True and res.run.tool_select["widened_why"] == "herramienta_no_ofrecida"
    assert llm.offered[1] == llm.offered[2] and res.answer == "ok"  # una sola ampliación


def test_a_declared_limitation_with_a_subset_widens_exactly_once_and_reconsults() -> None:
    res, llm = spy_agent([final(LIMIT), final(LIMIT)])
    ts = res.run.tool_select
    assert ts["widened"] is True and ts["widened_why"] == "limitacion_declarada_con_subconjunto"
    assert len(llm.offered) == 2 and len(llm.offered[0]) < len(llm.offered[1]) == 6  # tras ampliar, todas
    assert res.guardrail == "limitacion_sin_tools"  # la segunda declaración se acepta: no hay una segunda ampliación ni un bucle


def test_the_widening_message_is_appended_and_every_previous_message_is_kept() -> None:
    llm = Script([final(LIMIT), final(LIMIT)])
    seen: list[list[Any]] = []
    real = llm.complete

    def spy(system: str, messages: list[Any], tools: list[dict[str, Any]]) -> Any:
        seen.append(list(messages))
        return real(system, messages, tools)

    llm.complete = spy  # type: ignore[method-assign]
    asyncio.run(Agent(llm, FakeMcp(), 6, tools_mode="dynamic").run(Q, []))
    assert len(seen[1]) == len(seen[0]) + 2 and seen[1][-1].role == "user" and "todas las herramientas" in seen[1][-1].text
    assert [m.text for m in seen[1][: len(seen[0])]] == [m.text for m in seen[0]]


def test_full_mode_never_widens_and_never_adds_the_message() -> None:
    llm = Script([final(LIMIT), final(LIMIT)])
    res = asyncio.run(Agent(llm, FakeMcp(), 6, tools_mode="full").run(Q, []))
    assert len(llm.seen) == 1 and res.run.tool_select is None


def test_faltas_gets_the_acts_tools_and_search_rules() -> None:
    sel = select_tools("¿Cuántas faltas lleva el DOSA?")
    assert sel.tools is not None and {"get_match_report", "list_match_events", "search_rules", "find_teams", "list_matches"} <= sel.tools


@pytest.mark.parametrize(("question", "must"), [
    ("¿Quién arbitró el último partido del DOSA?", {"get_match_report", "list_matches", "find_teams"}),
    ("¿Qué tarjetas vio el DOSA en el último partido?", {"get_match_report", "list_match_events"}),
    ("¿Quién ha marcado más goles en el grupo 3?", {"get_scorer_table", "top_scorers", "list_match_events", "get_match_report"}),
    ("¿Cuántos goles ha marcado Lucas Casillas?", {"find_players", "get_scorer_table", "list_match_events"}),
    ("¿Qué dice el reglamento sobre la sexta falta?", {"search_rules"}),
    ("¿Cuánto dura un tiempo muerto?", {"search_rules"}),
])
def test_acts_scorers_and_rules_questions_get_what_they_need(question: str, must: set[str]) -> None:
    sel = select_tools(question)
    assert must <= (sel.tools if sel.tools is not None else ALL_TOOLS)


def test_official_scorers_and_acts_are_always_offered_together_and_the_policy_is_not_in_the_selector() -> None:
    """La política de goleadores (tabla oficial frente a actas) vive en el prompt y en las herramientas; la selección no quita ninguna de las dos vías."""
    sel = select_tools("¿Quién es el máximo goleador del grupo 3?")
    assert sel.tools is not None and {"get_scorer_table", "list_match_events", "get_match_report"} <= sel.tools


@pytest.mark.parametrize("question", ["¿Y el siguiente partido?", "¿Cuántos goles lleva?", "¿Y comparado con el otro equipo?", "Ahora dime la clasificación de la copa"])
def test_follow_ups_and_scope_changes_always_get_the_full_set(question: str) -> None:
    assert select_tools(question, has_context=True).tools is None
    res, llm = spy_agent([call("find_teams", name="C.D. DOSA"), final("ok")], question, history=[Turn("¿Cómo va el C.D. DOSA?", "bien")])
    assert len(llm.offered[0]) == 6 and res.run.tool_select["reason"] == "context"  # FakeMcp expone 6 y llegan todas


def test_a_focus_without_history_also_gets_the_full_set() -> None:
    llm = Script([call("find_teams", name="C.D. DOSA"), final("ok")])
    res = asyncio.run(Agent(llm, FakeMcp(), 6, tools_mode="dynamic").run("¿Cuántas faltas lleva el DOSA?", [], focus={"team": "C.D. DOSA"}))
    assert res.run.tool_select["reason"] == "context" and res.run.tool_select["offered"] == 6


def test_dynamic_presets_exist_and_reference_real_evaluator_cases() -> None:
    from futsal.agent.arms import PRESETS
    src = "".join((Path(__file__).parent.parent / "src" / "futsal" / "agent" / f).read_text(encoding="utf-8") for f in ("evaluate.py", "audit_cases.py", "rule_cases.py"))
    for name in ("dynamic_general", "dynamic_actas", "dynamic_rules", "dynamic_scorers", "dynamic_ambiguous", "tier0_probe"):
        assert PRESETS[name] and len(set(PRESETS[name])) == len(PRESETS[name]), name
        for case_id in PRESETS[name]:
            assert f'"{case_id}"' in src, (name, case_id)
