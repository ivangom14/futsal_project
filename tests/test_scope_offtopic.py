"""Preguntas de liga sin equipos («¿Cómo va la liga preferente?») no se dan por fuera de tema, y la hora de los partidos llega al LLM en Europe/Madrid."""

import asyncio
import json
from typing import Any

import pytest
from tests.test_competition_resolution import Mcp, Script

from futsal.agent.agent import OFF_TOPIC_MESSAGE, Agent, localize_match_dates
from futsal.agent.llm import LLMResponse, ToolCall
from futsal.agent.reconcile import to_madrid_iso


def run(question: str, steps: list[LLMResponse], mcp: Mcp | None = None) -> tuple[Script, Any]:
    llm = Script(steps)
    return llm, asyncio.run(Agent(llm, mcp or Mcp(), 5).run(question, []))


@pytest.mark.parametrize("q", ["¿Cómo va la liga preferente?", "¿Cómo va la liga juvenil?", "¿Cómo va la tercera división?"])
def test_recognized_competition_without_teams_is_reconsulted_instead_of_refused(q: str) -> None:
    llm, res = run(q, [LLMResponse(OFF_TOPIC_MESSAGE, []), LLMResponse("", [ToolCall("c1", "list_matches", {})]), LLMResponse("Clasificación.", [])])
    assert len(llm.seen) == 3 and res.tool_calls == 1 and res.answer == "Clasificación."


def test_really_off_topic_question_is_still_refused_in_one_call() -> None:
    llm, res = run("¿Qué tiempo hará mañana en Madrid?", [LLMResponse(OFF_TOPIC_MESSAGE, [])])
    assert len(llm.seen) == 1 and res.tool_calls == 0 and res.answer == OFF_TOPIC_MESSAGE


def test_to_madrid_iso() -> None:
    assert to_madrid_iso("2026-11-07T17:30:00+00:00") == "2026-11-07T18:30:00+01:00"
    assert to_madrid_iso("2026-07-07T17:30:00+00:00") == "2026-07-07T19:30:00+02:00"
    assert to_madrid_iso("2026-11-07T17:30:00Z") == "2026-11-07T18:30:00+01:00"
    for bad in ("2026-11-07", "2026-11-07T17:30:00", "basura", None, 5):
        assert to_madrid_iso(bad) is None


def test_localize_match_dates_list_single_and_untouched() -> None:
    lst = json.loads(localize_match_dates(json.dumps({"matches": [{"id": 1, "date": "2026-11-07T17:30:00+00:00"}, {"id": 2, "date": None}], "count": 2})))
    assert lst["matches"][0]["date"] == "2026-11-07T18:30:00+01:00" and lst["matches"][1]["date"] is None
    one = json.loads(localize_match_dates(json.dumps({"id": 3, "date": "2026-07-01T20:00:00+00:00"})))
    assert one["date"] == "2026-07-01T22:00:00+02:00"
    for raw in ("no json", "[1,2]", json.dumps({"matches": [{"date": "2026-11-07"}]})):
        assert localize_match_dates(raw) == raw


def test_llm_sees_madrid_hour_in_list_matches() -> None:
    class M(Mcp):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
            if name == "list_matches":
                return False, json.dumps({"matches": [{"id": 1, "home": "A", "away": "B", "date": "2026-11-07T17:30:00+00:00"}], "count": 1})
            return await super().call_tool(name, arguments)

    llm, _ = run("¿Próximos partidos de la liga preferente?", [LLMResponse("", [ToolCall("c1", "list_matches", {})]), LLMResponse("ok", [])], M())
    tool_msg = next(m for m in llm.seen[1] if m.role == "tool")
    assert "2026-11-07T18:30:00+01:00" in tool_msg.tool_results[0].content
