"""Fase 0: instrumentación de tokens. Agente REAL con LLM guionizado y MCP simulado (sin cuota, sin red, sin PostgreSQL).

Comprueba que el reparto de la entrada se registra distinguiendo lo REAL (proveedor) de lo ESTIMADO, que las trazas antiguas se siguen
leyendo y que medir no cambia lo que se envía al LLM."""

import asyncio
import json
from pathlib import Path
from typing import Any

from futsal.agent.agent import HISTORY_RULE, SYSTEM_PROMPT, Agent
from futsal.agent.llm import LLMResponse, Message, ToolCall, ToolResult, Usage
from futsal.agent.memory import Turn
from futsal.agent.tokens import CHARS_PER_TOKEN, PRICES, Price, est_tokens, estimate_cost, input_breakdown
from futsal.agent.trace import read_jsonl, write_jsonl

FIXTURES = Path(__file__).parent / "fixtures"
COMPETITIONS = {"competitions": [{"id": 1, "name": "TERCERA DIVISIÓN FS", "groups": [{"id": 6, "name": "Grupo 3"}]}]}
STANDINGS = {"group_id": 6, "status": "ok", "source": {"round": 5}, "standings": [{"position": 1, "team": "C.D. DOSA", "points": 10}]}


class FakeMcp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}}
                for n in ("list_competitions", "get_standings", "list_rounds", "list_teams")]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        self.calls.append((name, dict(arguments)))
        if name == "list_competitions":
            return False, json.dumps(COMPETITIONS)
        if name == "get_standings":
            return False, json.dumps(STANDINGS)
        return False, json.dumps({"items": [], "count": 0})


class Script:
    provider, model = "fake", "fake-model"

    def __init__(self, steps: list[LLMResponse]) -> None:
        self.steps = list(steps)
        self.seen: list[tuple[str, list[Message], list[dict[str, Any]]]] = []

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.seen.append((system, [Message(m.role, m.text, list(m.tool_calls), list(m.tool_results)) for m in messages], tools))
        return self.steps.pop(0)


def _usage(inp: int, out: int, *, estimated: bool = False, cached: int | None = None) -> Usage:
    return Usage(inp, out, 0, inp + out, estimated=estimated, cached_input_tokens=cached)


def _two_steps(estimated: bool = False) -> list[LLMResponse]:
    return [LLMResponse("", [ToolCall("t1", "get_standings", {"group_id": 6})], _usage(3000, 20, estimated=estimated, cached=0)),
            LLMResponse("Lidera el C.D. DOSA con 10 puntos.", [], _usage(3400, 15, estimated=estimated, cached=0))]


def _run(llm: Script, question: str = "¿Quién lidera la clasificación de Tercera División FS grupo 3?", **kw: Any) -> Any:
    history = kw.pop("history", ())
    trace = kw.pop("trace_path", None)
    cid = kw.pop("conversation_id", None)
    agent = Agent(llm, FakeMcp(), 5, trace_path=trace, **kw)
    return asyncio.run(agent.run(question, history, conversation_id=cid))


# --- tokens.py: reparto por origen ---------------------------------------------------------------------------------------------
def test_estimate_uses_the_documented_ratio_and_zero_is_zero() -> None:
    assert est_tokens(0) == 0 and est_tokens(7) == 2 and est_tokens(35) == 10 and CHARS_PER_TOKEN == 3.5


def test_breakdown_assigns_every_message_to_exactly_one_origin() -> None:
    msgs = [Message("user", "pregunta anterior"), Message("assistant", "respuesta anterior"),
            Message("user", "¿y ahora? NOTA-APP"),
            Message("assistant", "", [ToolCall("c", "get_standings", {"group_id": 6})]),
            Message("tool", "", tool_results=[ToolResult("c", "x" * 100)]),
            Message("user", "reconsulta")]
    b = input_breakdown(system_prompt_chars=70, history_rule_chars=7, context_chars=35, tools_chars=140, messages=msgs,
                        history_messages=2, question_chars=len("¿y ahora?"), note_chars=len(" NOTA-APP"))
    c = b["chars"]
    assert c["system_prompt"] == 70 and c["history_rule"] == 7 and c["context"] == 35 and c["tools"] == 140
    assert c["history"] == len("pregunta anterior") + len("respuesta anterior")
    assert c["question"] == len("¿y ahora?") and c["scope_notes"] == len(" NOTA-APP")
    assert c["tool_results"] == 100 and c["assistant_turns"] > 0 and c["other"] == len("reconsulta")
    assert b["est_tokens"]["tools"] == 40 and b["est_total"] == sum(b["est_tokens"].values())
    assert b["basis"].startswith("estimated") and b["provider_input"] is None and b["calibration"] is None


def test_breakdown_calibration_only_exists_when_the_provider_reported() -> None:
    kw: dict[str, Any] = dict(system_prompt_chars=350, history_rule_chars=0, context_chars=0, tools_chars=0,
                              messages=[Message("user", "x" * 35)], history_messages=0, question_chars=35, note_chars=0)
    assert input_breakdown(**kw)["calibration"] is None
    real = input_breakdown(**kw, provider_input_tokens=220)
    assert real["provider_input"] == 220 and real["calibration"] == round(220 / real["est_total"], 3)


# --- precios: estructura lista, sin tarifas inventadas ------------------------------------------------------------------------
def test_no_prices_are_invented_and_estimated_tokens_are_never_costed() -> None:
    assert PRICES == {}
    totals = {"token_source": "provider", "input_tokens": 1000, "billed_output_tokens": 100, "cached_input_tokens": 0}
    assert estimate_cost(totals, None) is None
    price = Price(1.0, 2.0, source="prueba", verified_on="2026-01-01", cached_input_per_mtok=0.5)
    assert estimate_cost({**totals, "token_source": "estimated"}, price) is None  # estimado: no se presenta como coste facturado
    cost = estimate_cost({**totals, "cached_input_tokens": 400}, price)
    assert cost is not None and cost["usd"] == round(600 * 1.0 / 1e6 + 100 * 2.0 / 1e6 + 400 * 0.5 / 1e6, 8) and cost["price_source"] == "prueba"


# --- integración con el agente real --------------------------------------------------------------------------------------------
def test_each_llm_call_records_its_breakdown_with_real_and_estimated_kept_apart() -> None:
    res = _run(Script(_two_steps()))
    calls = res.run.llm_calls
    assert len(calls) == 2 and all(c.input_breakdown for c in calls)
    first, second = (c.input_breakdown for c in calls)
    assert first["provider_input"] == 3000 and second["provider_input"] == 3400  # lo informado por el proveedor
    assert first["est_total"] != 3000 and first["basis"].startswith("estimated")  # lo estimado se etiqueta como tal
    assert first["est_tokens"]["system_prompt"] == est_tokens(len(SYSTEM_PROMPT))
    assert first["chars"]["tool_results"] == 0 and second["chars"]["tool_results"] > 0  # los resultados solo viajan en la 2.ª llamada
    assert second["chars"]["assistant_turns"] > 0
    tb = res.run.totals()["token_breakdown"]
    assert tb["calls"] == 2 and tb["provider_input"] == 6400 and tb["est_total"] == first["est_total"] + second["est_total"]
    assert res.run.totals()["input_tokens"] == 6400 and res.run.totals()["token_source"] == "provider"


def test_estimated_provider_usage_is_not_reported_as_real() -> None:
    res = _run(Script(_two_steps(estimated=True)))
    assert all(c.input_breakdown["provider_input"] is None for c in res.run.llm_calls)
    t = res.run.totals()
    assert t["token_source"] == "estimated" and t["token_breakdown"]["provider_input"] is None and t["token_breakdown"]["calibration"] is None


def test_history_and_history_rule_have_their_own_origins() -> None:
    llm = Script([LLMResponse("Lidera el C.D. DOSA.", [], _usage(100, 5))])
    # sin herramientas el guardarraíl lo sustituye; basta con la primera llamada para el reparto
    res = _run(llm, "¿Y su posición?", history=[Turn("¿Cómo va el C.D. DOSA?", "Va primero.")])
    first = res.run.llm_calls[0].input_breakdown
    assert first["chars"]["history"] == len("¿Cómo va el C.D. DOSA?") + len("Va primero.")
    assert first["chars"]["history_rule"] == len(HISTORY_RULE) and first["chars"]["question"] == len("¿Y su posición?")


def test_instrumentation_does_not_change_what_is_sent_to_the_llm() -> None:
    """Misma ejecución con el router en sombra activado y desactivado: mismos mensajes, system, herramientas y llamadas MCP."""
    runs = []
    for shadow in (True, False):
        llm, agent_mcp = Script(_two_steps()), FakeMcp()
        agent = Agent(llm, agent_mcp, 5, router_shadow=shadow)
        res = asyncio.run(agent.run("¿Quién lidera la clasificación de Tercera División FS grupo 3?"))
        runs.append((llm.seen, agent_mcp.calls, res.answer, res.tool_calls, res.guardrail, res.error, len(res.run.llm_calls)))
    assert runs[0] == runs[1]


def test_trace_has_architecture_fields_and_counts_mcp_calls_by_origin(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    res = _run(Script(_two_steps()), trace_path=path, conversation_id="conv-1")
    row = read_jsonl(path)[0]
    assert row["arch"] == "legacy" and row["exec_tier"] == "tier2" and row["conversation_id"] == "conv-1"
    assert row["router_tier"] in ("tier0", "tier1", "tier2") and row["router_intent"] and row["router"]["tier0_eligible"] in (True, False)
    mcp = row["totals"]["mcp_calls"]
    assert mcp["requested_by_llm"] == 1 and mcp["context_preload"] >= 1 and mcp["refused"] == 0 and mcp["reused"] == 0
    assert row["totals"]["token_breakdown"]["calls"] == 2 and res.run.router_ms >= 0


def test_repeated_identical_call_is_marked_as_reused() -> None:
    steps = [LLMResponse("", [ToolCall("a", "get_standings", {"group_id": 6})], _usage(10, 1)),
             LLMResponse("", [ToolCall("b", "get_standings", {"group_id": 6})], _usage(10, 1)),
             LLMResponse("Lidera el C.D. DOSA.", [], _usage(10, 1))]
    res = _run(Script(steps))
    assert [t.reused for t in res.run.tool_calls] == [False, True] and res.run.totals()["mcp_calls"]["reused"] == 1


# --- trazas antiguas ----------------------------------------------------------------------------------------------------------
def test_old_trace_without_phase0_fields_is_still_readable() -> None:
    rows = read_jsonl(FIXTURES / "trace_before_phase0.jsonl")
    assert len(rows) == 1
    row = rows[0]
    assert "arch" not in json.loads((FIXTURES / "trace_before_phase0.jsonl").read_text().splitlines()[0])  # fixture genuinamente antiguo
    assert row["arch"] == "legacy" and row["exec_tier"] == "tier2" and row["router"] is None and row["conversation_id"] is None
    assert row["llm_calls"][0]["input_breakdown"] is None and row["tool_calls"][0]["reused"] is False
    assert row["totals"]["input_tokens"] == 6400 and row["totals"]["token_source"] == "provider"  # lo antiguo se conserva intacto


def test_read_jsonl_skips_blank_and_corrupt_lines_and_keeps_unknown_fields(tmp_path: Path) -> None:
    p = tmp_path / "x.jsonl"
    p.write_text('\n{"run_id":"a","future_field":1}\nnot json\n[1,2]\n', encoding="utf-8")
    rows = read_jsonl(p)
    assert len(rows) == 1 and rows[0]["future_field"] == 1 and rows[0]["arch"] == "legacy"


def test_new_trace_round_trips_through_write_and_read(tmp_path: Path) -> None:
    res = _run(Script(_two_steps()))
    p = tmp_path / "r.jsonl"
    write_jsonl(p, res.run)
    row = read_jsonl(p)[0]
    assert row["totals"]["token_breakdown"]["est_total"] == res.run.totals()["token_breakdown"]["est_total"]
