"""Tests del agente: LLM simulado (guion) + servidor MCP real en memoria con API simulada."""

import asyncio
import json
from typing import Any

import httpx
from mcp.shared.memory import create_connected_server_and_client_session as connect

from futsal.agent.agent import Agent, AgentResult
from futsal.agent.llm import AnthropicLLM, LLMResponse, Message, ToolCall
from futsal.agent.mcp_client import SessionMcpClient
from futsal.mcp_server.server import create_server

TOOLS = {"list_competitions", "list_groups", "list_rounds", "list_teams", "list_matches",
         "get_match"}


def _api(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/matches/2":
        return httpx.Response(200, json={"id": 2, "home_score": 3, "away_score": 4})
    if request.url.path.startswith("/matches/"):
        return httpx.Response(404, json={"detail": "no encontrado"})
    return httpx.Response(200, json={"matches": [{"id": 2}], "count": 1})


class ScriptedLLM:
    """Devuelve respuestas predefinidas y guarda lo que recibe en cada llamada."""

    def __init__(self, script: list[LLMResponse]) -> None:
        self.script = list(script)
        self.calls: list[tuple[list[dict[str, Any]], list[Message]]] = []

    def complete(self, system: str, messages: list[Message],
                 tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls.append((tools, [Message(m.role, m.text, m.tool_calls, m.tool_results)
                                   for m in messages]))
        return self.script.pop(0) if self.script else self._loop()

    @staticmethod
    def _loop() -> LLMResponse:
        return LLMResponse("", [ToolCall("x", "get_match", {"match_id": 2})])


def _call(name: str, **args: Any) -> LLMResponse:
    return LLMResponse("", [ToolCall("t1", name, args)])


def _run(llm: ScriptedLLM, question: str, max_calls: int = 5) -> AgentResult:
    server = create_server(httpx.Client(base_url="http://api", transport=httpx.MockTransport(_api)))

    async def go() -> AgentResult:
        async with connect(server._mcp_server) as session:
            return await Agent(llm, SessionMcpClient(session), max_calls).run(question)

    return asyncio.run(go())


def test_discovers_mcp_tools_and_gives_them_to_llm() -> None:
    llm = ScriptedLLM([LLMResponse("hola", [])])
    _run(llm, "¿Qué puedes hacer?")
    tools = llm.calls[0][0]
    assert {t["name"] for t in tools} == TOOLS
    assert all(t["description"] and t["input_schema"] for t in tools)


def test_tool_call_executes_and_result_returns_to_llm() -> None:
    llm = ScriptedLLM([_call("get_match", match_id=2), LLMResponse("Quedó 3-4", [])])
    res = _run(llm, "¿Cómo quedó el partido 2?")
    assert res.answer == "Quedó 3-4" and res.tool_calls == 1 and res.error is None
    last = llm.calls[1][1][-1]
    assert last.role == "tool" and not last.tool_results[0].is_error
    assert json.loads(last.tool_results[0].content)["home_score"] == 3
    assert any(line.startswith("MCP") for line in res.trace)


def test_multiple_tool_calls_in_one_question() -> None:
    llm = ScriptedLLM([_call("list_teams", group_id=1), _call("list_matches", group_id=1),
                       LLMResponse("fin", [])])
    res = _run(llm, "¿Qué partidos juega X?")
    assert res.tool_calls == 2 and res.answer == "fin"


def test_no_tool_question_runs_no_tools() -> None:
    res = _run(ScriptedLLM([LLMResponse("Puedo consultar partidos", [])]), "¿Qué puedes hacer?")
    assert res.tool_calls == 0 and res.answer == "Puedo consultar partidos"


def test_max_tool_calls_stops_infinite_loop() -> None:
    llm = ScriptedLLM([])  # pide get_match indefinidamente
    res = _run(llm, "bucle", max_calls=3)
    assert res.tool_calls == 3 and res.error and "límite" in res.error and res.answer == ""
    assert len(llm.calls) == 4


def test_mcp_error_is_returned_to_llm() -> None:
    llm = ScriptedLLM([_call("get_match", match_id=999999), LLMResponse("No existe", [])])
    res = _run(llm, "¿Cómo quedó el partido 999999?")
    result = llm.calls[1][1][-1].tool_results[0]
    assert result.is_error and "error_no_encontrado" in result.content
    assert res.answer == "No existe" and res.error is None


def test_anthropic_adapter_wire_format() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content), key=request.headers["x-api-key"])
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "ok"},
                        {"type": "tool_use", "id": "a", "name": "get_match",
                         "input": {"match_id": 2}}],
            "usage": {"input_tokens": 5, "output_tokens": 3}})

    llm = AnthropicLLM("k", "m", client=httpx.Client(base_url="http://llm",
                                                     transport=httpx.MockTransport(handler)))
    out = llm.complete("sys", [Message("user", "q")], [{"name": "t", "description": "d",
                                                         "input_schema": {}}])
    assert out.tool_calls == [ToolCall("a", "get_match", {"match_id": 2})]
    assert seen["system"] == "sys" and seen["messages"] == [{"role": "user", "content": "q"}]
    assert llm.usage == {"input_tokens": 5, "output_tokens": 3}
