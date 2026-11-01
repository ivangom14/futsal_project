"""Futsito, Bloque 6: valoración de respuestas (👍/👎, motivo y comentario) registrada para análisis, sin alterar al agente."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from futsal.agent.feedback import REASONS
from futsal.agent.web import AgentRunner, create_chat_app


def client(tmp_path: Path) -> tuple[TestClient, Path]:
    path = tmp_path / "fb" / "feedback.jsonl"
    return TestClient(create_chat_app(AgentRunner(), lambda: {"reachable": True, "detail": None}, feedback_path=path)), path


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_positive_rating_is_saved_with_conversation_message_and_date(tmp_path: Path) -> None:
    c, path = client(tmp_path)
    res = c.post("/chat/feedback", json={"conversation_id": "conv-1", "message_id": "msg-9", "rating": "up", "reason": "wrong_data",
                                         "comment": "no debería guardarse"})
    assert res.status_code == 200 and res.json() == {"saved": True}
    (entry,) = lines(path)
    assert (entry["conversation_id"], entry["message_id"], entry["rating"]) == ("conv-1", "msg-9", "up") and entry["ts"]
    assert entry["reason"] is None and entry["comment"] is None  # motivo y comentario solo con 👎


@pytest.mark.parametrize("reason", REASONS)
def test_negative_rating_keeps_each_reason_and_the_comment(tmp_path: Path, reason: str) -> None:
    c, path = client(tmp_path)
    c.post("/chat/feedback", json={"conversation_id": "c", "message_id": "m", "rating": "down", "reason": reason, "comment": "  Faltan datos  "})
    (entry,) = lines(path)
    assert entry["reason"] == reason and entry["comment"] == "Faltan datos"


def test_feedback_persists_appending_and_never_stores_secrets_or_answer_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-key-123456")
    c, path = client(tmp_path)
    c.post("/chat/feedback", json={"conversation_id": "c", "message_id": "m1", "rating": "down", "reason": "other",
                                   "comment": "mi clave es super-secret-key-123456"})
    c.post("/chat/feedback", json={"conversation_id": "c", "message_id": "m2", "rating": "up"})
    rows = lines(path)
    assert [r["message_id"] for r in rows] == ["m1", "m2"] and "super-secret" not in path.read_text(encoding="utf-8")
    assert set(rows[0]) == {"ts", "conversation_id", "message_id", "rating", "reason", "comment", "run_id", "diagnostics"}


@pytest.mark.parametrize("body", [
    {"conversation_id": "c", "message_id": "m", "rating": "meh"}, {"conversation_id": "c", "message_id": "m", "rating": "down", "reason": "x"},
    {"conversation_id": "c", "message_id": "m", "rating": "down", "comment": "x" * 1001}, {"conversation_id": "no válido!", "message_id": "m", "rating": "up"},
    {"conversation_id": "c", "rating": "up"}])
def test_invalid_feedback_is_rejected_and_nothing_is_written(tmp_path: Path, body: dict[str, Any]) -> None:
    c, path = client(tmp_path)
    assert c.post("/chat/feedback", json=body).status_code == 400 and not path.exists()


def test_diagnostics_summary_is_attached_from_the_server_not_the_browser(tmp_path: Path) -> None:
    from tests.test_futsito_presentation import Mcp, Script, call  # reutiliza el agente guionizado
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    from futsal.agent.agent import Agent
    from futsal.agent.llm import LLMResponse

    @asynccontextmanager
    async def factory() -> AsyncIterator[Agent]:
        yield Agent(Script([call("get_standings", group_id=12), LLMResponse("Líder DOSA.", [])]), Mcp(), 5, trace_results=True)

    path = tmp_path / "fb.jsonl"
    with TestClient(create_chat_app(AgentRunner(factory), lambda: {"reachable": True, "detail": None}, feedback_path=path)) as c:
        run_id = c.post("/chat", json={"question": "¿Cómo va?", "conversation_id": "c1"}).json()["diagnostics"]["run_id"]
        c.post("/chat/feedback", json={"conversation_id": "c1", "message_id": "m1", "rating": "down", "reason": "incomplete", "run_id": run_id})
        c.post("/chat/feedback", json={"conversation_id": "c1", "message_id": "m2", "rating": "down", "reason": "other", "run_id": "falso"})
    first, second = lines(path)
    assert first["diagnostics"]["tools"] == ["get_standings"] and first["diagnostics"]["answer_kind"] == "official"
    assert second["diagnostics"] is None  # un run_id desconocido no se acepta como diagnóstico
