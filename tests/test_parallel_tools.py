"""Herramientas en paralelo (T01a): misma salida que la ejecución secuencial, y tiempo ≈ máximo en vez de suma. Agente REAL, LLM guionizado, MCP simulado."""

import asyncio
import json
import random
import time
from dataclasses import asdict
from typing import Any

import pytest

from futsal.agent.agent import Agent
from futsal.agent.llm import LLMResponse, ToolCall
from test_prefetch import FakeMcp, Script, final

Q = "En TERCERA DIVISIÓN FS (Grupo 3), ¿cuál fue el último resultado del C.D. DOSA?"


class SlowMcp(FakeMcp):
    """FakeMcp con retraso y fallos deterministas según (herramienta, argumentos)."""

    def __init__(self, delay: float = 0.0, fail: bool = False) -> None:
        super().__init__()
        self.delay, self.fail = delay, fail
        self.live = self.peak = 0

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.live -= 1
        if self.fail:
            h = sum(map(ord, name + json.dumps(arguments, sort_keys=True))) % 7
            if h == 0:
                raise RuntimeError("boom")
            if h == 1:
                self.calls.append((name, dict(arguments)))
                return True, "error_dependencia: API no disponible"
        return await super().call_tool(name, arguments)


def batch(*specs: tuple[str, dict[str, Any]]) -> LLMResponse:
    return LLMResponse("", [ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(specs)])


def run(steps: list[LLMResponse], mcp: FakeMcp, parallel: bool) -> tuple[Any, Script]:
    llm = Script(steps)
    agent = Agent(llm, mcp, 20)
    if not parallel:
        agent._batch_independent = lambda calls: False  # type: ignore[method-assign]
    return asyncio.run(agent.run(Q, [])), llm


def shape(res: Any, llm: Script) -> dict[str, Any]:
    tcs = [{k: v for k, v in asdict(t).items() if k != "duration_ms"} for t in res.run.tool_calls]
    return {"answer": res.answer, "n": res.tool_calls, "official": res.official, "tcs": tcs,
            "messages": [[(m.role, m.text, [(c.id, c.name, c.arguments) for c in m.tool_calls],
                           [(r.call_id, r.content, r.is_error) for r in m.tool_results]) for m in msgs] for _, msgs in llm.seen]}


def test_two_tools_in_one_response_take_about_the_max_not_the_sum() -> None:
    steps = [batch(("find_teams", {"name": "DOSA"}), ("list_competitions", {})), final()]
    mcp = SlowMcp(0.3)
    t0 = time.perf_counter()
    res, _ = run(steps, mcp, True)
    par = time.perf_counter() - t0
    seq_mcp = SlowMcp(0.3)
    t0 = time.perf_counter()
    run([batch(("find_teams", {"name": "DOSA"}), ("list_competitions", {})), final()], seq_mcp, False)
    seq = time.perf_counter() - t0
    assert res.tool_calls == 2 and mcp.peak == 2 and seq_mcp.peak == 1
    assert seq - par > 0.25  # ahorra ≈ un retraso completo
    assert [t.name for t in res.run.tool_calls] == ["find_teams", "list_competitions"]


def test_sibling_ids_force_sequential_so_behaviour_is_unchanged() -> None:
    """Hoy una llamada puede usar un team_id que mostró su hermana en la misma respuesta: ahí no se paraleliza."""
    steps = [batch(("find_teams", {"name": "DOSA"}), ("list_matches", {"team_id": 11, "group_id": 6})), final()]
    res, _ = run(steps, SlowMcp(0.0), True)
    assert not any(t.refused for t in res.run.tool_calls) and res.tool_calls == 2
    agent = Agent(Script([]), FakeMcp(), 5)
    assert not agent._batch_independent([ToolCall("a", "find_teams", {"name": "x"}), ToolCall("b", "list_matches", {"team_id": 1})])
    assert not agent._batch_independent([ToolCall("a", "find_teams", {"name": "x"}), ToolCall("b", "find_teams", {"name": "x"})])
    assert agent._batch_independent([ToolCall("a", "find_teams", {"name": "x"}), ToolCall("b", "list_competitions", {})])


def two_finds_then_two_matches() -> list[LLMResponse]:
    return [batch(("find_teams", {"name": "DOSA"}), ("find_teams", {"name": "RODILLITO"})),
            batch(("list_matches", {"team_id": 11, "group_id": 6}), ("list_matches", {"team_id": 12, "group_id": 6})), final()]


def test_interleaved_calls_adding_ids_to_the_same_sets() -> None:
    """Dos find_teams concurrentes añaden IDs a los mismos conjuntos: estado y uso posterior de ambos IDs coinciden con el secuencial."""
    a, la = run(two_finds_then_two_matches(), SlowMcp(0.01), True)
    b, lb = run(two_finds_then_two_matches(), SlowMcp(0.01), False)
    assert shape(a, la) == shape(b, lb)
    assert not any(t.refused for t in a.run.tool_calls[2:])  # los IDs de las dos hermanas quedaron registrados


POOL = [("find_teams", {"name": "DOSA"}), ("find_teams", {"name": "RODILLITO"}), ("list_competitions", {}), ("list_rounds", {"group_id": 6}),
        ("get_standings", {"group_id": 6}), ("get_standings", {"group_id": 7}), ("list_matches", {"team_id": 11, "group_id": 6}),
        ("list_matches", {"team_id": 999, "group_id": 6}), ("list_matches", {"group_id": 6, "status": "finished"}), ("list_teams", {"group_id": 6})]


@pytest.mark.parametrize("seed", range(60))
def test_property_parallel_output_equals_sequential(seed: int) -> None:
    def script() -> list[LLMResponse]:
        rng = random.Random(seed)
        return [batch(*(rng.choice(POOL) for _ in range(rng.randint(1, 4)))) for _ in range(rng.randint(1, 3))] + [final()]
    fail = seed % 3 == 0
    a, la = run(script(), SlowMcp(0.0, fail), True)
    b, lb = run(script(), SlowMcp(0.0, fail), False)
    assert shape(a, la) == shape(b, lb)
