"""Reparación determinista de un `group_id` omitido (AGENT_GROUP_ID_REPAIR, apagada por defecto). Agente REAL con LLM guionizado y MCP simulado; sin red ni cuota.

No demuestra que el fallo `prefetch-r4` quede resuelto (no se tiene su traza): comprueba solo que la reparación actúa en los casos conservadores previstos."""

import asyncio
from typing import Any

import pytest

from futsal.agent.agent import Agent, resolve_group_repair
from test_prefetch import FakeMcp, Script, call, final

Q = "En TERCERA DIVISIÓN FS (Grupo 3), ¿cuál fue el último resultado del C.D. DOSA?"


def run(steps: list[Any], question: str = Q, repair: str | None = "on", mcp: FakeMcp | None = None) -> tuple[Any, FakeMcp]:
    fake = mcp or FakeMcp()
    res = asyncio.run(Agent(Script(steps), fake, 6, group_repair=repair).run(question, []))
    return res, fake


def matches_calls(fake: FakeMcp) -> list[dict[str, Any]]:
    return [a for n, a in fake.calls if n == "list_matches"]


def listing(res: Any) -> Any:
    return next(t for t in res.run.tool_calls if t.name == "list_matches")


def test_default_is_off_and_unknown_values_are_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_GROUP_ID_REPAIR", raising=False)
    assert resolve_group_repair() is False and resolve_group_repair("on") is True and resolve_group_repair("quizás") is False


def test_off_behaves_exactly_as_before() -> None:
    res, fake = run([call("find_teams", name="C.D. DOSA"), call("list_matches", team_id=11, status="finished", limit=1), final()], repair="off")
    rec = listing(res)
    assert matches_calls(fake) == [{"team_id": 11, "status": "finished", "limit": 1}]  # sin group_id: se envía tal cual
    assert rec.repaired is None and rec.arguments == rec.original_arguments == {"team_id": 11, "status": "finished", "limit": 1}


def test_missing_group_id_is_completed_when_the_team_is_in_a_single_group_and_both_argument_sets_are_kept() -> None:
    res, fake = run([call("find_teams", name="C.D. DOSA"), call("list_matches", team_id=11, status="finished", limit=1), final()])
    rec = listing(res)
    assert matches_calls(fake) == [{"team_id": 11, "status": "finished", "limit": 1, "group_id": 6}]
    assert rec.original_arguments == {"team_id": 11, "status": "finished", "limit": 1}  # lo que envió el modelo
    assert rec.arguments == {"team_id": 11, "status": "finished", "limit": 1, "group_id": 6}  # lo que se ejecutó
    assert rec.repaired == {"field": "group_id", "value": 6, "source": "find_teams", "team_id": 11, "reason": "group_id_missing_single_group_for_team_id"}
    assert res.run.totals()["mcp_calls"]["repaired"] == 1 and not rec.is_error


def test_an_explicit_group_id_is_never_overwritten() -> None:
    res, fake = run([call("find_teams", name="C.D. DOSA"), call("list_matches", team_id=11, group_id=7), final()])
    rec = listing(res)
    assert matches_calls(fake) == [{"team_id": 11, "group_id": 7}] and rec.repaired is None and rec.original_arguments == rec.arguments


def test_a_team_without_a_verifiable_origin_is_not_repaired_and_keeps_the_old_refusal() -> None:
    res, fake = run([call("list_matches", team_id=11), final()])
    rec = res.run.tool_calls[0]
    assert not matches_calls(fake) and rec.refused and rec.repaired is None and rec.original_arguments == {"team_id": 11}


def test_ambiguous_homonym_is_not_repaired() -> None:
    res, fake = run([call("find_teams", name="DIAGONAL"), call("list_matches", team_id=13), final()], "¿Cuándo juega el F.S. DIAGONAL 'A'?")
    rec = listing(res)
    assert not matches_calls(fake) and rec.refused and rec.repaired is None


def test_call_without_team_id_is_not_repaired() -> None:
    res, fake = run([call("find_teams", name="C.D. DOSA"), call("list_matches", status="finished"), final()])
    assert matches_calls(fake) == [{"status": "finished"}] and all(t.repaired is None for t in res.run.tool_calls)


def test_repair_state_does_not_leak_between_questions() -> None:
    fake = FakeMcp()
    agent = Agent(Script([call("find_teams", name="C.D. DOSA"), call("list_matches", team_id=11), final("ok")]), fake, 6, group_repair="on")
    asyncio.run(agent.run(Q, []))
    assert agent._team_groups[11] == {6}
    agent._llm.steps = [call("list_matches", team_id=11), final("ok")]  # type: ignore[attr-defined]
    res = asyncio.run(agent.run(Q, []))
    assert agent._team_groups == {} and res.run.tool_calls[0].repaired is None and res.run.tool_calls[0].refused


def test_the_evaluator_judges_the_models_own_arguments() -> None:
    from futsal.agent.evaluate import verdict_calls
    res, _ = run([call("find_teams", name="C.D. DOSA"), call("list_matches", team_id=11), final()])
    assert ("list_matches", '{"team_id": 11}', False) in verdict_calls(res.run.tool_calls)
