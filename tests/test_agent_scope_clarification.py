"""Aclaraciones legítimas de ámbito (competición/grupo/temporada): se conservan; el resto de protecciones sigue activo. Sin LLM real."""
import asyncio
from typing import Any

import httpx

from futsal.agent.agent import OFF_TOPIC_MESSAGE, Agent, AgentResult
from futsal.agent.criteria import asks_scope_clarification, scope_identified
from futsal.agent.llm import LLMResponse, Message, ToolCall
from futsal.agent.mcp_client import SessionMcpClient
from futsal.mcp_server.server import create_server

CATALOG = [("LIGA UNO FS", ["Grupo 1", "Grupo 2"]), ("COPA DOS FS", ["Eliminatorias"])]
ASK = "Para indicarte el máximo goleador necesito que me digas a qué competición y grupo te refieres."


def _api(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/competitions":
        comps = [{"id": i, "name": c, "groups": [{"id": i * 10 + j, "name": g} for j, g in enumerate(gs, 1)]}
                 for i, (c, gs) in enumerate(CATALOG, 1)]
        return httpx.Response(200, json={"items": comps, "competitions": comps, "count": len(comps)})
    if path == "/groups/11/scorer-table":
        return httpx.Response(200, json={"status": "ok", "scorers": [{"player": "PEREZ, ANA", "goals": 5}]})
    if path == "/groups/99/standings":
        return httpx.Response(503, json={"detail": "caída"})
    return httpx.Response(404, json={"detail": "no encontrado"})


class Script:
    def __init__(self, script: list[LLMResponse]) -> None:
        self.script, self.calls = list(script), 0

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls += 1
        return self.script.pop(0) if self.script else LLMResponse("fin", [])


def _run(llm: Script, question: str) -> AgentResult:
    server = create_server(httpx.Client(base_url="http://api", transport=httpx.MockTransport(_api)))

    async def go() -> AgentResult:
        async with __import__("mcp.shared.memory", fromlist=["x"]).create_connected_server_and_client_session(server._mcp_server) as s:
            return await Agent(llm, SessionMcpClient(s), 5).run(question)  # type: ignore[arg-type]

    return asyncio.run(go())


def test_scorer_question_without_group_keeps_the_clarification() -> None:
    llm = Script([LLMResponse(ASK, [])])
    res = _run(llm, "¿Quién es el máximo goleador del grupo?")
    assert res.answer == ASK and res.guardrail == "aclaracion_ambito" and res.tool_calls == 0 and llm.calls == 1  # sin reconsulta forzada


def test_combined_scorer_and_position_question_without_group_keeps_the_clarification() -> None:
    text = "¿Podrías indicarme la competición y el grupo? Hay varios grupos disponibles."
    res = _run(Script([LLMResponse(text, [])]), "¿Quién es el máximo goleador del grupo y en qué puesto está su equipo?")
    assert res.answer == text and res.guardrail == "aclaracion_ambito"


def test_season_clarification_is_also_allowed_and_options_with_group_numbers_are_not_figures() -> None:
    text = "¿De qué temporada y grupo hablas? Tengo LIGA UNO FS (Grupo 1, Grupo 2) y COPA DOS FS (Eliminatorias)."
    assert _run(Script([LLMResponse(text, [])]), "¿Quién lidera la clasificación?").guardrail == "aclaracion_ambito"


def test_question_that_names_competition_and_group_follows_the_normal_flow() -> None:
    question = "En LIGA UNO FS (Grupo 1), ¿quién es el máximo goleador?"
    assert scope_identified(question, CATALOG)
    llm = Script([LLMResponse(ASK, []), LLMResponse("", [ToolCall("t", "get_scorer_table", {"group_id": 11})]),
                  LLMResponse("Lidera PEREZ, ANA con 5 goles (tabla oficial).", [])])
    res = _run(llm, question)
    assert res.guardrail is None and res.tool_calls == 1 and "5 goles" in res.answer   # la petición de aclaración innecesaria no se acepta


def test_genuinely_off_topic_questions_are_still_rejected_even_with_a_clarification_shaped_answer() -> None:
    res = _run(Script([LLMResponse(ASK, [])]), "Cuéntame un chiste")
    assert res.answer == OFF_TOPIC_MESSAGE and res.guardrail == "sin_datos_de_tools"


def test_invented_figures_are_not_accepted_just_because_they_come_with_a_question() -> None:
    for text in ("El máximo goleador tiene 12 goles. ¿De qué grupo hablas?", "Va 3-1. ¿Puedes indicarme el grupo?",
                 "Quedó primero con 9 puntos, ¿qué competición?"):
        res = _run(Script([LLMResponse(text, []), LLMResponse(text, [])]), "¿Quién es el máximo goleador del grupo?")
        assert res.guardrail == "sin_datos_de_tools" and res.answer == OFF_TOPIC_MESSAGE, text


def test_a_bare_question_or_unrelated_request_is_not_a_scope_clarification() -> None:
    for text in ("¿Qué quieres saber?", "Va ganando el Club A, ¿te interesa algo más?", "Hay datos disponibles.", ""):
        assert not asks_scope_clarification(text, CATALOG), text
    assert asks_scope_clarification(ASK, CATALOG) and not asks_scope_clarification(ASK + " " * 700, CATALOG[:0] and CATALOG)


def test_clarification_after_tools_follows_the_previous_flow_and_tool_errors_stay_errors() -> None:
    llm = Script([LLMResponse("", [ToolCall("t", "get_standings", {"group_id": 99})]),
                  LLMResponse("No he podido consultar la clasificación: la API no responde.", [])])
    res = _run(llm, "¿Quién lidera el grupo?")
    assert res.guardrail is None and res.tool_calls == 1 and not any(ch.isdigit() for ch in res.answer)


def test_legitimate_data_limitations_are_still_accepted() -> None:
    res = _run(Script([LLMResponse("No dispongo de los minutos de los cambios en mis herramientas.", [])]),
               "¿En qué minuto entraron los suplentes del partido de la jornada 3 en LIGA UNO FS (Grupo 1)?")
    assert res.guardrail == "limitacion_sin_tools"


def test_clarification_that_repeats_the_round_number_of_the_question_is_kept() -> None:
    text = "Para decirte cómo quedaron los partidos de la jornada 2, necesito que me indiques a qué competición y grupo te refieres."
    res = _run(Script([LLMResponse(text, [])]), "¿Cómo quedaron los partidos de la jornada 2?")
    assert res.answer == text and res.guardrail == "aclaracion_ambito"
    invented = "En la jornada 2 ganó el Club A por 3 goles. ¿A qué grupo te refieres?"
    res2 = _run(Script([LLMResponse(invented, []), LLMResponse(invented, [])]), "¿Cómo quedaron los partidos de la jornada 2?")
    assert res2.guardrail == "sin_datos_de_tools" and res2.answer == OFF_TOPIC_MESSAGE
