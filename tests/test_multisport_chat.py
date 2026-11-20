"""Fase 1 multideporte en el servicio de chat: `sport` obligatorio en `/chat`, `/chat/feedback` y `/chat/crests`, propagación al
agente (y de ahí a cada herramienta MCP) y aislamiento de la memoria cuando una conversación cambia de deporte."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_chat_memory import FakeMcp, Script

from futsal.agent.agent import Agent
from futsal.agent.llm import LLMResponse, ToolCall
from futsal.agent.memory import ConversationMemory, Turn
from futsal.agent.web import AgentRunner, create_chat_app

ANSWER = "El líder es el C.D. DOSA con 30 puntos."


class ScopedMcp(FakeMcp):
    """MCP simulado cuyas herramientas declaran `sport` (como el servidor real): registra el deporte que recibe cada llamada."""

    def __init__(self) -> None:
        super().__init__()
        self.sports: list[str | None] = []
        self.listed: set[str] = set()
        self.sport_calls: list[tuple[str, str | None]] = []

    async def list_tools(self) -> list[dict[str, Any]]:
        tools = await super().list_tools()
        extra = {"list_teams", "list_rounds", "list_groups", "get_standings", "get_match", "get_match_report"} - {t["name"] for t in tools}
        tools = [*tools, *({"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}} for n in sorted(extra))]
        self.listed = {t["name"] for t in tools}
        return [{**t, "input_schema": {**t["input_schema"], "properties": {"sport": {"type": "string"},
                                                                          **t["input_schema"].get("properties", {})}}}
                for t in tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        self.sports.append(arguments.get("sport"))
        self.sport_calls.append((name, arguments.get("sport")))
        return await super().call_tool(name, {k: v for k, v in arguments.items() if k != "sport"})


def web(steps: list[LLMResponse]) -> tuple[TestClient, Script, ScopedMcp]:
    llm, mcp = Script(steps), ScopedMcp()

    @asynccontextmanager
    async def factory() -> AsyncIterator[Agent]:
        yield Agent(llm, mcp, 5, trace_results=True)

    return TestClient(create_chat_app(AgentRunner(factory), lambda: {"reachable": True, "detail": None},
                                      freshness=lambda ids, sport: {})), llm, mcp


@pytest.mark.parametrize("body", [{"question": "hola"}, {"question": "hola", "sport": "all"}, {"question": "hola", "sport": "tenis"}])
def test_chat_requires_an_explicit_sport(body: dict[str, Any]) -> None:
    c, _, _ = web([])
    with c:
        r = c.post("/chat", json=body)
    assert r.status_code == 400 and "sport" in r.json()["detail"]


def test_feedback_and_crests_require_sport() -> None:
    c, _, _ = web([])
    with c:
        fb = {"conversation_id": "c", "message_id": "m", "rating": "up"}
        assert c.post("/chat/feedback", json=fb).status_code == 400
        assert c.get("/chat/crests").status_code == 400
        assert c.get("/chat/crests", params={"sport": "football11"}).status_code == 200


def test_the_request_sport_reaches_every_tool_call() -> None:
    steps = [LLMResponse("", [ToolCall("c1", "get_standings", {"group_id": 1})]), LLMResponse(ANSWER, [])]
    c, _, mcp = web(steps)
    with c:
        r = c.post("/chat", json={"question": "¿Quién lidera?", "sport": "football11", "conversation_id": "conv-1"})
    assert r.status_code == 200 and len(mcp.sport_calls) >= 3
    assert {sp for _, sp in mcp.sport_calls} == {"football11"}  # también las del contexto precargado y el catálogo de equipos


def test_switching_sport_in_a_conversation_drops_its_context() -> None:
    c, llm, _ = web([LLMResponse(ANSWER, []), LLMResponse("Respuesta de fútbol.", [])])
    with c:
        c.post("/chat", json={"question": "¿Quién lidera la liga?", "sport": "futsal", "conversation_id": "conv-1"})
        c.post("/chat", json={"question": "¿Y el segundo?", "sport": "football11", "conversation_id": "conv-1",
                              "history": [{"role": "user", "content": "¿Quién lidera la liga?"}, {"role": "assistant", "content": ANSWER}],
                              "focus": {"team": "C.D. DOSA"}})
    second = " ".join(text for _, text in llm.seen[-1])
    assert "DOSA" not in second and "¿Quién lidera la liga?" not in second  # ni historial ni foco del otro deporte


def test_memory_binds_one_sport_per_conversation() -> None:
    mem = ConversationMemory()
    assert not mem.bind_sport("c", "futsal")
    mem.put("c", [Turn("q", "r")])
    mem.put_focus("c", {"team": "X"})
    assert not mem.bind_sport("c", "futsal") and mem.get("c") == [Turn("q", "r")]
    assert mem.bind_sport("c", "football11") and mem.get("c") is None and mem.get_focus("c") is None
    assert mem.get_sport("c") == "football11" and mem.forget("c") is False and mem.get_sport("c") is None


def test_agent_keeps_one_catalog_per_sport() -> None:
    mcp = ScopedMcp()
    llm = Script([LLMResponse(ANSWER, []), LLMResponse(ANSWER, []), LLMResponse(ANSWER, [])])
    agent = Agent(llm, mcp, 5)
    asyncio.run(agent.run("¿Quién lidera?", sport="futsal"))
    fs_system = llm.systems[-1]
    asyncio.run(agent.run("¿Quién lidera?", sport="football11"))
    assert agent.sport == "football11" and "fútbol (fútbol 11)" in llm.systems[-1] and "fútbol sala RFFM" not in llm.systems[-1]
    asyncio.run(agent.run("¿Quién lidera?", sport="futsal"))
    assert llm.systems[-1] == fs_system  # fútbol sala recupera su propio prompt y catálogo, sin mezclas


# --- Fase 2: mención explícita de otro deporte (determinista, sin LLM) ------------------------------------------------------------


@pytest.mark.parametrize("sport,question,switch", [
    ("football11", "¿Cómo va la Primera Senior de fútbol sala?", "futsal"),  # competición del catálogo (simulado) activo
    ("futsal", "¿Qué partidos de fútbol hay este sábado?", "football11"),
    ("futsal", "Clasificación de la Primera Juvenil de fútbol 11", "football11"),
    ("futsal", "¿Qué partidos hay de fútbol y de fútbol sala?", "football11"),
])
def test_naming_another_sport_proposes_a_switch_without_calling_the_llm(sport: str, question: str, switch: str) -> None:
    c, llm, mcp = web([])  # sin pasos: cualquier llamada al LLM fallaría
    with c:
        body = c.post("/chat", json={"question": question, "sport": sport, "conversation_id": "conv-x"}).json()
    assert body["sport_switch"] == switch and body["guardrail"] == "otro_deporte" and llm.seen == []
    assert not [n for n, _ in mcp.sport_calls if n not in ("list_competitions", "list_teams")]  # ni datos del otro deporte
    assert {sp for _, sp in mcp.sport_calls} <= {sport}


@pytest.mark.parametrize("sport,question", [("futsal", "¿Cómo va el DOSA en fútbol sala?"), ("football11", "Clasificación de Preferente de fútbol"),
                                            ("futsal", "¿Quién lidera la Preferente?"), ("futsal", "Partidos de fútbol 7 alevín"),
                                            ("futsal", "¿Quién ganó el último Mundial de fútbol 11?"),  # fuera de ámbito: rechazo normal
                                            ("football11", "¿Quién ganó la Champions de fútbol sala?")])
def test_same_sport_or_no_sport_mention_is_answered_normally(sport: str, question: str) -> None:
    c, llm, _ = web([LLMResponse(ANSWER, [])])
    with c:
        body = c.post("/chat", json={"question": question, "sport": sport}).json()
    assert "sport_switch" not in body and body["guardrail"] != "otro_deporte" and len(llm.seen) == 1  # contrato de siempre


def test_off_topic_message_names_the_selected_sport() -> None:
    from futsal.agent.agent import OFF_TOPIC_MESSAGES, PROMPTS_BY_SPORT, is_off_topic
    assert "fútbol sala" in OFF_TOPIC_MESSAGES["futsal"] and "fútbol (fútbol 11)" in OFF_TOPIC_MESSAGES["football11"]
    assert OFF_TOPIC_MESSAGES["football11"] in PROMPTS_BY_SPORT["football11"].system and "fútbol sala" not in PROMPTS_BY_SPORT["football11"].system.split("Herramientas v2")[0]
    assert all(is_off_topic(m) for m in OFF_TOPIC_MESSAGES.values()) and not is_off_topic("El líder es X")


def test_short_follow_up_naming_the_other_sport_proposes_the_switch() -> None:
    llm = Script([])
    agent = Agent(llm, ScopedMcp(), 5)
    res = asyncio.run(agent.run("¿Y en futsal?", [Turn("¿Quién lidera la Primera Senior, grupo 1?", ANSWER)], sport="football11"))
    assert res.sport_switch == "futsal" and llm.seen == []
    alone = asyncio.run(Agent(Script([LLMResponse(ANSWER, [])]), ScopedMcp(), 5).run("¿Y en futsal?", sport="football11"))
    assert alone.sport_switch is None  # sin conversación previa, «¿y en futsal?» no basta para saber que es de competición
