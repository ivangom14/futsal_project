"""Límite de herramientas agotado: respuesta honesta y no vacía, sin datos sin verificar. LLM guionizado, sin red."""
import asyncio
import re
from typing import Any

import httpx
from mcp.shared.memory import create_connected_server_and_client_session as connect

from futsal.agent.agent import LIMIT_REACHED_MESSAGE, OFF_TOPIC_MESSAGE, Agent, AgentResult
from futsal.agent.llm import LLMResponse, Message, ToolCall
from futsal.agent.mcp_client import SessionMcpClient
from futsal.mcp_server.server import create_server


def _api(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/matches/2":
        return httpx.Response(200, json={"id": 2, "home_score": 3, "away_score": 4})
    if request.url.path.startswith("/matches/"):
        return httpx.Response(404, json={"detail": "no encontrado"})
    if request.url.path == "/competitions":
        comps = [{"id": i, "name": f"LIGA {i} FS", "groups": [{"id": i * 10 + 1, "name": "Grupo 1"}, {"id": i * 10 + 2, "name": "Grupo 2"}]}
                 for i in (1, 2)]
        return httpx.Response(200, json={"items": comps, "competitions": comps, "count": 2})
    return httpx.Response(200, json={"matches": [{"id": 2}], "count": 1})


class Script:
    def __init__(self, script: list[LLMResponse]) -> None:
        self.script = list(script)

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        return self.script.pop(0) if self.script else LLMResponse("", [ToolCall("x", "get_match", {"match_id": 2})])


def _run(llm: Script, question: str, max_calls: int = 3) -> AgentResult:
    server = create_server(httpx.Client(base_url="http://api", transport=httpx.MockTransport(_api)))

    async def go() -> AgentResult:
        async with connect(server._mcp_server) as session:
            return await Agent(llm, SessionMcpClient(session), max_calls).run(question)  # type: ignore[arg-type]

    return asyncio.run(go())


def _call(name: str, **args: Any) -> LLMResponse:
    return LLMResponse("", [ToolCall("t", name, args)])


def test_limit_without_a_final_answer_gives_an_honest_non_empty_message_and_keeps_the_diagnosis() -> None:
    res = _run(Script([]), "¿Cómo quedó el último partido del líder?")
    assert res.answer == LIMIT_REACHED_MESSAGE and res.answer.strip() and res.guardrail == "limite_herramientas"
    assert res.error == "límite de 3 tool calls alcanzado"            # el diagnóstico sigue ahí (código de salida, evaluador, traza)
    assert res.run is not None and res.run.answer == LIMIT_REACHED_MESSAGE and res.run.error == res.error
    assert not re.search(r"\d", res.answer) and "no existe" not in res.answer.lower()   # ni cifras ni «no existe»
    assert "no he podido completar" in res.answer.lower()


def test_a_valid_final_answer_before_the_limit_is_kept_unchanged() -> None:
    res = _run(Script([_call("get_match", match_id=2), LLMResponse("El partido 2 terminó 3-4.", [])]), "¿Cómo quedó el partido 2?")
    assert res.answer == "El partido 2 terminó 3-4." and res.error is None and res.guardrail is None


def test_the_models_unverified_text_is_not_shown_when_the_limit_is_hit() -> None:
    risky = LLMResponse("Quedó 3-1, seguro.", [ToolCall("a", "get_match", {"match_id": 2}), ToolCall("b", "get_match", {"match_id": 2}),
                                              ToolCall("c", "get_match", {"match_id": 2}), ToolCall("d", "get_match", {"match_id": 2})])
    res = _run(Script([risky]), "¿Cómo quedó el partido?")
    assert res.tool_calls == 0 and res.answer == LIMIT_REACHED_MESSAGE and "3-1" not in res.answer     # un lote que excede el límite no se ejecuta


def test_tool_errors_never_become_figures_when_the_limit_is_reached() -> None:
    res = _run(Script([_call("get_match", match_id=999999)] * 6), "¿Cómo quedó el partido 999999?")
    assert res.tool_calls == 3 and res.answer == LIMIT_REACHED_MESSAGE and not re.search(r"\d", res.answer)
    assert res.error is not None and "límite" in res.error


def test_a_tool_error_followed_by_an_honest_answer_is_not_touched_by_the_limit_logic() -> None:
    res = _run(Script([_call("get_match", match_id=999999), LLMResponse("No he podido consultar ese partido.", [])]), "¿Partido 999999?")
    assert res.answer == "No he podido consultar ese partido." and res.error is None and res.guardrail is None


def test_legitimate_scope_clarifications_still_work() -> None:
    ask = "Para responder necesito que me indiques a qué competición y grupo te refieres."
    res = _run(Script([LLMResponse(ask, [])]), "¿Quién es el máximo goleador del grupo?")
    assert res.answer == ask and res.guardrail == "aclaracion_ambito" and res.error is None


def test_genuinely_off_topic_questions_are_still_rejected() -> None:
    res = _run(Script([LLMResponse("Claro, aquí tienes un chiste.", [])]), "Cuéntame un chiste")
    assert res.answer == OFF_TOPIC_MESSAGE and res.guardrail == "sin_datos_de_tools"


def test_other_failures_are_not_swallowed_into_the_limit_message() -> None:
    from futsal.agent.llm import LLMError

    class Boom:
        def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
            raise LLMError("proveedor caído")

    res = _run(Boom(), "¿Cómo quedó el partido 2?")  # type: ignore[arg-type]
    assert res.answer == "" and res.error and "proveedor caído" in res.error and res.guardrail is None
