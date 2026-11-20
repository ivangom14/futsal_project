"""Comprensión conversacional determinista: el agente real con LLM guionizado y el MCP simulado del evaluador (sin red ni cuota)."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from futsal.agent.agent import OFF_TOPIC_MESSAGE, Agent
from futsal.agent.llm import LLMResponse, Message, ToolCall
from futsal.agent.memory import ConversationMemory, Turn
from futsal.agent.understanding_eval import CJF, CS1P, PREF_A, PREF_J, TER, FakeMcp
from futsal.agent.web import AgentRunner, create_chat_app


class Seq:
    """LLM guionizado: devuelve las respuestas en orden."""

    provider, model = "fake", "fake"

    def __init__(self, steps: list[LLMResponse | Exception]) -> None:
        self.steps, self.seen = list(steps), []

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.seen.append(list(messages))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def run(llm: Any, question: str, history: list[Turn] | None = None, state: dict[str, Any] | None = None) -> Any:
    return asyncio.run(Agent(llm, FakeMcp(), 8).run(question, history or [], state=state, sport="futsal"))


def call(tool: str, **args: Any) -> LLMResponse:
    return LLMResponse("", [ToolCall("c", tool, args)])


def last_user(llm: Seq) -> str:
    return next(m.text for m in reversed(llm.seen[0]) if m.role == "user")


def test_capture_case_inherits_juvenil_and_declares_the_assumption_once() -> None:
    history = [Turn("Y el Dosa juvenil, cómo va?", "C.D. DOSA aparece en: PRIMERA DIVISION AUTONOMICA JUVENIL FS, PREFERENTE JUVENIL FS (Grupos 1 y 2), COPA JUVENIL - fase final- FS "
                    "y PRIMERA JUVENIL FS. ¿Cuál quieres consultar?")]
    llm = Seq([LLMResponse("", [ToolCall("a", "get_standings", {"group_id": 1101}), ToolCall("b", "get_standings", {"group_id": 1102})]), LLMResponse("Líder X.", [])])
    res = run(llm, "¿Cómo va la liga preferente?", history)
    note = last_user(llm)
    assert PREF_J in note and PREF_A not in note and "group_id=1101" in note and "group_id=1102" in note and "Intención identificada: standings" in note
    assert res.answer == "Entiendo que te refieres a PREFERENTE JUVENIL FS. Líder X." and res.tool_calls == 2
    again = run(Seq([call("get_scorer_table", group_id=1101), call("get_scorer_table", group_id=1102), LLMResponse("Goleador Y.", [])]),
                "¿Y los goleadores?", [*history, Turn("¿Cómo va la liga preferente?", res.answer)], state=res.state)
    assert "Entiendo que" not in again.answer  # el contexto ya está establecido: no se repite


def test_inherited_context_never_blocks_a_new_team_or_competition() -> None:
    res = run(Seq([LLMResponse(OFF_TOPIC_MESSAGE, [])]), "¿Cómo va el DOSA?", [Turn("¿Cómo va la tercera división?", "…")])
    # antes (legacy): find_teams devolvía 0 filas con «no figura en ese ámbito»; ahora la tercera no oculta las 6 competiciones reales del equipo
    assert res.run is not None and res.run.context["status"] == "ask" and len(res.run.context["options"]) == 6 and res.tool_calls == 0
    cup = run(Seq([LLMResponse(OFF_TOPIC_MESSAGE, [])]), "¿Cómo va el DOSA en la copa?", [Turn("¿Cómo va la tercera división?", "…")])
    assert cup.run.context["status"] == "ask" and set(cup.run.context["options"]) == {CS1P, CJF}  # el contexto senior no esconde la copa juvenil del equipo
    llm = Seq([call("find_teams", name="DOSA"), LLMResponse("ok", [])])
    run(llm, "¿Cómo va el DOSA en la copa?", [Turn("¿Cómo va la tercera división?", "…")])
    rows = json.loads(next(m for m in llm.seen[1] if m.role == "tool").tool_results[0].content)["teams"]
    # sin ámbito resuelto, find_teams se filtra solo con lo que dice el usuario («la copa»): las 2 copas del equipo; la tercera heredada no esconde la juvenil
    assert {r["competition"] for r in rows} == {CS1P, CJF}


def test_state_survives_memory_loss_by_rebuilding_from_history() -> None:
    first = run(Seq([call("get_standings", group_id=1401), LLMResponse("Tabla.", [])]), "Clasificación de la primera juvenil grupo 5")
    turns = [Turn("Clasificación de la primera juvenil grupo 5", first.answer)]
    kept = run(Seq([call("get_scorer_table", group_id=1405), LLMResponse("G.", [])]), "¿Y los goleadores?", turns, state=first.state)
    lost = run(Seq([call("get_scorer_table", group_id=1405), LLMResponse("G.", [])]), "¿Y los goleadores?", turns, state=None)
    assert kept.run.context["state"] == "stored" and lost.run.context["state"] == "rebuilt"
    assert kept.run.context["groups"] == lost.run.context["groups"] and kept.run.context["competitions"] == lost.run.context["competitions"]
    stale = run(Seq([call("get_scorer_table", group_id=1405), LLMResponse("G.", [])]), "¿Y los goleadores?", [Turn("otra cosa distinta", "x")], state=first.state)
    assert stale.run.context["state"] == "rebuilt"  # un estado que no corresponde al historial se descarta


def test_ambiguity_asks_with_real_options_and_never_consults() -> None:
    res = run(Seq([LLMResponse(OFF_TOPIC_MESSAGE, [])]), "¿Cómo va la preferente?")
    assert res.tool_calls == 0 and res.guardrail == "aclaracion_ambito" and PREF_A in res.answer and PREF_J in res.answer and res.answer.endswith("?")
    assert res.state is not None and res.state["offered"] == [PREF_A, PREF_J]


def test_missing_and_other_seasons_are_explained_honestly() -> None:
    miss = run(Seq([LLMResponse(OFF_TOPIC_MESSAGE, [])]), "Clasificación de la segunda división")
    assert miss.tool_calls == 0 and "segunda division" in miss.answer and TER in miss.answer and miss.guardrail == "limitacion_sin_tools"
    season = run(Seq([LLMResponse(OFF_TOPIC_MESSAGE, [])]), "¿Cómo quedó la tercera el año pasado?")
    assert season.tool_calls == 0 and "temporadas anteriores" in season.answer
    off = run(Seq([LLMResponse(OFF_TOPIC_MESSAGE, [])]), "¿Quién ganó el mundial de 2018?")
    assert off.answer == OFF_TOPIC_MESSAGE and off.run.context["status"] == "none"  # lo ajeno se sigue rechazando


def test_recovery_revises_the_interpretation_then_runs_the_queries_itself() -> None:
    refuse = LLMResponse(OFF_TOPIC_MESSAGE, [])
    revised = run(Seq([refuse, call("get_standings", group_id=101), call("get_standings", group_id=102), LLMResponse("Tabla.", [])]), "¿Cómo va la tercera?")
    assert revised.run.context["recovery"] == "revision" and revised.answer.startswith("Entiendo") is False and revised.tool_calls == 2
    llm = Seq([refuse, refuse, LLMResponse("Con los datos.", [])])
    res = run(llm, "¿Cómo va la tercera?")  # el modelo sigue sin consultar: la aplicación ejecuta las consultas y se las entrega
    assert res.run.context["recovery"] == "consultas_de_la_aplicacion" and res.answer == "Con los datos." and {c.via for c in res.run.tool_calls} == {"recovery"}
    assert [c.name for c in res.run.tool_calls] == ["get_standings", "get_standings"] and "Datos consultados por la aplicación" in llm.seen[2][-1].text
    stubborn = run(Seq([refuse, refuse, refuse]), "¿Cómo va la tercera?")  # rechaza incluso con los datos: se entregan los verificados
    assert stubborn.guardrail == "recuperacion" and "líder" in stubborn.answer.lower() or "Esto es lo que consta" in stubborn.answer


def test_server_keeps_the_state_per_conversation_and_forgets_it() -> None:
    mem = ConversationMemory()
    mem.put_state("c", {"version": 1, "competitions": [TER]})
    assert mem.get_state("c") == {"version": 1, "competitions": [TER]} and mem.get_state("x") is None
    mem.put("c", [Turn("q", "r")])
    assert mem.forget("c") and mem.get_state("c") is None


def test_health_reports_the_context_mode_the_backend_started_with(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def factory() -> AsyncIterator[Agent]:
        yield Agent(Seq([]), FakeMcp(), 8)

    monkeypatch.setenv("AGENT_CONTEXT", "legacy")  # la variable ya no existe: la arquitectura es única
    client = TestClient(create_chat_app(AgentRunner(factory), lambda: {"reachable": True, "detail": None}))
    body = client.get("/chat/health").json()
    assert body["context_mode"] == "deterministic" and body["arch"] == "v2"


def test_web_service_persists_state_and_rebuilds_it_after_a_restart() -> None:
    def build(llm: Seq) -> TestClient:
        @asynccontextmanager
        async def factory() -> AsyncIterator[Agent]:
            yield Agent(llm, FakeMcp(), 8)

        return TestClient(create_chat_app(AgentRunner(factory), lambda: {"reachable": True, "detail": None}))

    llm = Seq([call("get_standings", group_id=101), call("get_standings", group_id=102), LLMResponse("Tabla.", []),
               call("get_scorer_table", group_id=101), call("get_scorer_table", group_id=102), LLMResponse("Goleadores.", [])])
    client = build(llm)
    r1 = client.post("/chat", json={"sport": "futsal", "question": "Clasificación de la tercera", "conversation_id": "c1", "history": []})
    assert r1.status_code == 200 and "state" not in r1.json()  # el estado no sale al cliente
    ctx = r1.json()["diagnostics"]["context"]  # …pero el panel de detalles técnicos sí ve cómo se interpretó
    assert ctx["mode"] == "deterministic" and ctx["status"] == "act" and ctx["competitions"] == [TER] and ctx["intent"] == "standings"
    r2 = client.post("/chat", json={"sport": "futsal", "question": "¿Y los goleadores?", "conversation_id": "c1", "history": []})
    assert r2.status_code == 200 and "Intención identificada: scorers" in next(m.text for m in reversed(llm.seen[3]) if m.role == "user")
    # el servidor se reinicia (memoria vacía): el navegador reenvía el historial y el contexto se reconstruye
    llm2 = Seq([call("get_standings", group_id=101), call("get_standings", group_id=102), LLMResponse("Tabla.", [])])
    r3 = build(llm2).post("/chat", json={"sport": "futsal", "question": "¿Y la clasificación?", "conversation_id": "c1", "history": [
        {"role": "user", "content": "Clasificación de la tercera"},
        {"role": "assistant", "content": r1.json()["answer_original"] or r1.json()["answer"]}]})  # como el navegador (conversations.historyFor)
    assert r3.status_code == 200 and TER in next(m.text for m in reversed(llm2.seen[0]) if m.role == "user")
