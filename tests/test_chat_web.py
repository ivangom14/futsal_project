"""Fase 22: servicio HTTP del chat. Agente REAL con LLM guionizado y MCP simulado (sin llamadas al LLM ni a PostgreSQL)."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from futsal.agent.agent import Agent
from futsal.agent.llm import LLMError, LLMResponse, Message, ToolCall
from futsal.agent.web import AgentRunner, ChatResponse, create_chat_app
from futsal.api.knowledge import payload

FIXTURE = Path(__file__).parent / "fixtures" / "chat_response.json"
QUESTION = "¿Cuánto dura cada mitad en fútbol sala?"


def _hit(text: str, rule: int = 7, pages: tuple[int, int] = (31, 31)) -> SimpleNamespace:
    return SimpleNamespace(document_title="Reglas de Juego de Fútbol Sala FIFA", doc_key="fifa-futsal", version_label="2025/26",
                           rule_number=rule, doc_type="reglamento", rule_title="La duración del partido", section_number="1",
                           section_title="Periodos de juego", subsection_title=None, page_start=pages[0], page_end=pages[1],
                           score=3.2, text=text, truncated=False, part=None, is_current=True)


def _search_payload() -> str:
    result = SimpleNamespace(query="duración mitad", method="lexical", hits=[
        _hit("El partido durará dos tiempos iguales de 20 minutos."), _hit("Segundo fragmento.", pages=(32, 32))], references=[])
    return json.dumps(payload(result))


class FakeMcp:
    def __init__(self, results: dict[str, str] | None = None) -> None:
        self.results = results or {}

    async def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}}
                for n in ("list_competitions", "search_rules", "get_standings")]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        if name == "list_competitions":
            return False, '{"competitions":[],"count":0}'
        return False, self.results.get(name, '{"error":"x"}')


class Script:
    provider, model = "fake", "fake-model"

    def __init__(self, steps: list[LLMResponse | Exception]) -> None:
        self.steps = list(steps)

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def client(steps: list[LLMResponse | Exception], mcp: FakeMcp | None = None) -> TestClient:
    @asynccontextmanager
    async def factory() -> AsyncIterator[Agent]:
        yield Agent(Script(steps), mcp or FakeMcp({"search_rules": _search_payload()}), 3, trace_results=True)

    return TestClient(create_chat_app(AgentRunner(factory), lambda: {"reachable": True, "detail": None}))


def rules_steps() -> list[LLMResponse | Exception]:
    return [LLMResponse("", [ToolCall("c1", "search_rules", {"query": "duración mitad"})]),
            LLMResponse("Cada mitad dura **20 minutos** (Regla 7, p. 31).", [])]


def normalized(body: dict[str, Any]) -> dict[str, Any]:
    d = body["diagnostics"]
    d["run_id"], d["duration_ms"] = "run", 0
    for t in d["tools"]:
        t["duration_ms"] = 0
    return body


def test_chat_returns_answer_sources_and_diagnostics() -> None:
    with client(rules_steps()) as c:
        res = c.post("/chat", json={"question": QUESTION})
    assert res.status_code == 200
    body = ChatResponse.model_validate(res.json())
    assert "20 minutos" in body.answer and body.error is None and body.guardrail is None
    assert [s.kind for s in body.sources] == ["rag", "rag"]
    assert body.sources[0].title.startswith("Reglas de Juego") and body.sources[0].pages == "31"
    assert body.sources[0].text and "20 minutos" in body.sources[0].text
    d = body.diagnostics
    assert [t.name for t in d.tools] == ["search_rules"] and d.llm_calls == 2 and d.model == "fake-model"


def test_contract_fixture_matches_backend() -> None:
    """El frontend prueba contra este mismo JSON: si cambia el contrato, este test obliga a regenerarlo."""
    with client(rules_steps()) as c:
        body = normalized(c.post("/chat", json={"question": QUESTION}).json())
    if not FIXTURE.exists():  # primera generación explícita
        FIXTURE.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == body


def test_no_sources_are_invented_without_rag() -> None:
    steps: list[LLMResponse | Exception] = [
        LLMResponse("", [ToolCall("c1", "get_standings", {})]), LLMResponse("El líder es X.", [])]
    mcp = FakeMcp({"get_standings": '{"error":"x"}'})
    with client(steps, mcp) as c:
        body = c.post("/chat", json={"question": "¿Quién lidera la clasificación?"}).json()
    assert body["sources"] == []


def test_official_table_is_a_source() -> None:
    table = json.dumps({"status": "ok", "competition": "Primera", "group": "Grupo 2", "group_id": 1,
                        "source": {"captured_at": "2026-10-20T10:00:00", "round": 3},
                        "standings": [{"position": 1, "team": "A", "points": 9}]})
    steps: list[LLMResponse | Exception] = [
        LLMResponse("", [ToolCall("c1", "get_standings", {})]), LLMResponse("Líder A.", [])]
    with client(steps, FakeMcp({"get_standings": table})) as c:
        body = c.post("/chat", json={"question": "¿Cómo va la clasificación?"}).json()
    assert body["sources"][0]["kind"] == "official_table" and body["sources"][0]["meta"]["round"] == 3


def test_llm_failure_is_http_error_not_an_answer() -> None:
    with client([LLMError("cuota agotada (429)")]) as c:
        res = c.post("/chat", json={"question": QUESTION})
    assert res.status_code == 502 and "cuota agotada" in res.json()["detail"]


def test_tool_limit_keeps_safe_answer_and_reports_error() -> None:
    calls = [LLMResponse("", [ToolCall(f"c{i}", "search_rules", {"query": f"q{i}"})]) for i in range(5)]
    with client(calls) as c:
        res = c.post("/chat", json={"question": QUESTION})
    body = res.json()
    assert res.status_code == 200 and body["error"] and body["answer"] and body["guardrail"] == "limite_herramientas"


@pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "x" * 2001}])
def test_invalid_input_is_400(body: dict[str, Any]) -> None:
    with client([]) as c:
        assert c.post("/chat", json=body).status_code == 400


def test_whitespace_question_is_400() -> None:
    with client([]) as c:
        assert c.post("/chat", json={"question": "   "}).status_code == 400


def test_agent_unavailable_is_503_and_visible_in_health(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def broken() -> AsyncIterator[Agent]:
        raise LLMError("falta GEMINI_API_KEY")
        yield  # pragma: no cover

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    app = create_chat_app(AgentRunner(broken), lambda: {"reachable": True, "detail": None})
    with TestClient(app) as c:
        assert c.post("/chat", json={"question": QUESTION}).status_code == 503
        health = c.get("/chat/health").json()
    assert health["status"] == "degraded" and "GEMINI_API_KEY" in health["agent"]["error"]
    assert health["llm"]["key_configured"] is False


def test_health_ok_never_exposes_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-key-123456")
    with client([]) as c:
        res = c.get("/chat/health")
    assert res.json()["status"] == "ok" and "super-secret" not in res.text


def test_cors_only_for_local_origins() -> None:
    with client([]) as c:
        ok = c.options("/chat", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
                                         "Access-Control-Request-Headers": "content-type"})
        bad = c.options("/chat", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in bad.headers
