"""Respuesta lenta o encolada: tiempo máximo por pregunta, cola visible, desglose de tiempos y logs con los reintentos del LLM."""

import logging
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from futsal.agent.agent import Agent
from futsal.agent.llm import Attempt, LLMResponse, Message
from futsal.agent.web import AgentRunner, create_chat_app


class SlowLLM:
    provider, model = "fake", "fake"

    def __init__(self, delays: list[float], retries: bool = False) -> None:
        self.delays, self.retries = list(delays), retries

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        time.sleep(self.delays.pop(0) if self.delays else 0)
        attempts = [Attempt(1, 429, "HTTP 429", 3.0), Attempt(2, 200, None, 0.0)] if self.retries else []
        return LLMResponse("Solo puedo responder preguntas sobre la competición.", [], None, attempts)


class NoTools:
    async def list_tools(self) -> list[dict[str, Any]]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        return False, "{}"


def client(llm: SlowLLM, timeout: float = 5.0) -> TestClient:
    @asynccontextmanager
    async def factory() -> AsyncIterator[Agent]:
        yield Agent(llm, NoTools(), 3, preload_context=False)

    return TestClient(create_chat_app(AgentRunner(factory), lambda: {"reachable": True, "detail": None}, question_timeout=timeout))


def post(c: TestClient, q: str = "Cuéntame un chiste") -> Any:
    return c.post("/chat", json={"question": q, "conversation_id": "c1"}, timeout=30)


def test_a_slow_question_times_out_with_a_friendly_error_and_frees_the_queue() -> None:
    with client(SlowLLM([1.5, 0]), timeout=0.4) as c:
        t = time.monotonic()
        res = post(c)
        assert res.status_code == 504 and res.json()["code"] == "timeout" and "tardando más de lo esperado" in res.json()["detail"]
        assert "tiempo máximo" in res.json()["technical"] and time.monotonic() - t < 1.2  # no esperó a que Gemini terminara
        assert post(c, "otra").status_code == 200  # el turno quedó libre: la siguiente pregunta no se encola detrás de la lenta


def test_health_stays_responsive_and_shows_the_queue_while_a_question_runs() -> None:
    with client(SlowLLM([1.2, 0])) as c:
        first = threading.Thread(target=lambda: post(c))
        first.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not c.get("/chat/health").json()["queue"]["busy"]:
            time.sleep(0.02)
        t = time.monotonic()
        health = c.get("/chat/health").json()
        assert time.monotonic() - t < 0.5  # el servicio responde aunque Gemini esté tardando
        assert health["queue"]["busy"] is True and health["queue"]["waiting"] == 0 and health["queue"]["running_for_s"] is not None
        second = threading.Thread(target=lambda: post(c, "segunda"))
        second.start()
        time.sleep(0.2)
        assert c.get("/chat/health").json()["queue"]["waiting"] == 1  # la segunda espera su turno
        first.join()
        second.join()
        assert c.get("/chat/health").json()["queue"] == {"busy": False, "running_for_s": None, "waiting": 0}


def test_diagnostics_break_down_where_the_time_went(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="futsal"), client(SlowLLM([0.3], retries=True)) as c:
        d = post(c).json()["diagnostics"]
    assert d["llm_ms"] >= 250 and d["tools_ms"] == 0 and d["llm_retries"] == 1 and d["retry_wait_ms"] == 3000.0
    assert {"context_ms", "postprocess_ms", "input_tokens_peak"} <= set(d)
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "1 reintento(s)" in text and "pregunta recibida" in text and "pregunta resuelta" in text  # visible en la ventana del servidor
