"""Fase 24 (3.5 y 3.6): mensajes «sin información» según el motivo y errores técnicos en lenguaje llano con el detalle en los logs."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from futsal.agent.agent import SYSTEM_PROMPT, Agent
from futsal.agent.criteria import states_tool_limit
from futsal.agent.llm import LLMError, LLMResponse
from futsal.agent.messages import (
    CONNECTION,
    INTERNAL,
    LIMITATION_REPLIES,
    NEEDS_DETAIL,
    NO_DATA,
    NO_INFO,
    RATE_LIMIT,
    TIMEOUT,
    UNCONFIRMED,
    UNSUPPORTED,
    classify_error,
)
from futsal.agent.web import AgentRunner, create_chat_app

LLM_429 = ("LLM HTTP 429: el servidor pide esperar 47s, por encima del máximo permitido (20s); no se reintenta")


@pytest.mark.parametrize("technical,expected", [
    (LLM_429, RATE_LIMIT), ("LLM HTTP 429: quota exceeded", RATE_LIMIT),
    ("fallo de red con el LLM: ConnectError (tras 3 intentos)", CONNECTION), ("LLM HTTP 503: unavailable", CONNECTION),
    ("fallo de red con el LLM: ReadTimeout", TIMEOUT), ("timed out", TIMEOUT),
    ("error_dependencia: McpError", INTERNAL), ("KeyError: 'x'", INTERNAL), ("respuesta inválida del LLM (ValueError)", INTERNAL)])
def test_technical_errors_are_classified(technical: str, expected: Any) -> None:
    assert classify_error(technical) is expected


def test_each_reason_has_its_own_natural_message() -> None:
    assert len(set(LIMITATION_REPLIES)) == 5 and NEEDS_DETAIL.startswith("¿") and UNSUPPORTED.startswith("Todavía")
    assert all(m in SYSTEM_PROMPT for m in (NO_DATA, NO_INFO, UNCONFIRMED, UNSUPPORTED, NEEDS_DETAIL))
    assert "No dispongo de ese dato en mis herramientas" not in SYSTEM_PROMPT  # ya no se usa siempre la misma frase genérica


@pytest.mark.parametrize("reply", [NO_DATA, NO_INFO, UNCONFIRMED, UNSUPPORTED])
def test_the_new_formulas_still_count_as_an_honest_limitation(reply: str) -> None:
    assert states_tool_limit(reply)


class NoTools:
    async def list_tools(self) -> list[dict[str, Any]]:
        return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        return False, "{}"


class Boom:
    provider, model = "fake", "fake"

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def complete(self, *args: Any) -> LLMResponse:
        raise self.exc


def client(llm: Any) -> TestClient:
    @asynccontextmanager
    async def factory() -> AsyncIterator[Agent]:
        yield Agent(llm, NoTools(), 3, preload_context=False)

    return TestClient(create_chat_app(AgentRunner(factory), lambda: {"reachable": True, "detail": None}))


@pytest.mark.parametrize("exc,status,code,text", [
    (LLMError(LLM_429), 429, "rate_limit", "Espera un poco y vuelve a intentarlo"),
    (LLMError("fallo de red con el LLM: ConnectError (tras 3 intentos)"), 502, "connection", "No he podido conectar con el servicio"),
    (LLMError("fallo de red con el LLM: ReadTimeout (tras 3 intentos)"), 504, "timeout", "tardando más de lo esperado"),
    (LLMError("respuesta inválida del LLM (ValueError)"), 500, "internal", "Ha ocurrido un problema al procesar tu consulta")])
def test_chat_shows_friendly_messages_and_keeps_the_technical_detail(
        exc: LLMError, status: int, code: str, text: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="futsal.chat"), client(Boom(exc)) as c:
        res = c.post("/chat", json={"question": "¿Quién lidera?"})
    body = res.json()
    assert res.status_code == status and body["code"] == code and text in body["detail"]
    assert "LLM" not in body["detail"] and "HTTP" not in body["detail"] and "429" not in body["detail"]  # nada técnico en el mensaje
    assert str(exc) in body["technical"]  # disponible para el panel de diagnóstico
    assert any(str(exc) in r.getMessage() for r in caplog.records)  # y en los logs


def test_unexpected_exception_is_a_friendly_internal_error_without_traceback(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="futsal.chat"), client(Boom(RuntimeError("fallo MCP interno"))) as c:
        res = c.post("/chat", json={"question": "¿Quién lidera?"})
    assert res.status_code == 500 and res.json()["code"] == "internal" and "Traceback" not in res.text
    assert any("fallo MCP interno" in r.getMessage() for r in caplog.records)


def test_unavailable_agent_is_friendly() -> None:
    @asynccontextmanager
    async def broken() -> AsyncIterator[Agent]:
        raise LLMError("GEMINI_API_KEY no definida (ver .env.example)")
        yield  # pragma: no cover

    with TestClient(create_chat_app(AgentRunner(broken), lambda: {"reachable": True, "detail": None})) as c:
        res = c.post("/chat", json={"question": "hola"})
    assert res.status_code == 503 and res.json()["code"] == "unavailable" and "GEMINI_API_KEY" not in res.json()["detail"]
    assert "GEMINI_API_KEY" in res.json()["technical"]


def test_secrets_never_reach_the_technical_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-key-123456")
    with client(Boom(LLMError("LLM HTTP 400: key=super-secret-key-123456 inválida"))) as c:
        res = c.post("/chat", json={"question": "hola"})
    assert "super-secret" not in res.text
