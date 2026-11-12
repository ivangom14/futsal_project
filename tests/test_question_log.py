"""Registro diario de preguntas (`agent/question_log.py`): sin LLM real, agente simulado como en `test_chat_web.py`."""

import json
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.test_chat_web import FakeMcp, Script

from futsal.agent.agent import Agent
from futsal.agent.llm import LLMError, LLMResponse
from futsal.agent.question_log import QuestionLog
from futsal.agent.web import AgentRunner, create_chat_app


def lines(folder: Path) -> list[dict[str, str]]:
    return [json.loads(x) for f in sorted(folder.glob("*.jsonl")) for x in f.read_text(encoding="utf-8").splitlines()]


def app_client(qlog: QuestionLog, steps: list[LLMResponse | Exception]) -> TestClient:
    @asynccontextmanager
    async def factory() -> AsyncIterator[Agent]:
        yield Agent(Script(steps), FakeMcp(), 3)

    return TestClient(create_chat_app(AgentRunner(factory), lambda: {"reachable": True, "detail": None}, question_log=qlog))


def test_one_line_per_request_with_only_ts_and_question(tmp_path: Path) -> None:
    qlog = QuestionLog(tmp_path, max_chars=40)
    with app_client(qlog, [LLMResponse("Hola", [])]) as c:
        assert c.post("/chat", json={"question": "  ¿Quién lidera? mi correo es pepe@x.com " + "a" * 100}).status_code == 200
        assert c.post("/chat", json={"question": "   "}).status_code == 400  # vacía: no es una pregunta
    rows = lines(tmp_path)
    assert len(rows) == 1 and set(rows[0]) == {"ts", "question"} and rows[0]["ts"].endswith("Z")
    assert rows[0]["question"] == "¿Quién lidera? mi correo es [email] a" and "Hola" not in json.dumps(rows)


def test_internal_retries_do_not_duplicate(tmp_path: Path) -> None:
    qlog = QuestionLog(tmp_path)
    with app_client(qlog, [LLMError("503 temporal"), LLMResponse("ok", [])]) as c:
        c.post("/chat", json={"question": "¿Cuántas jornadas hay?"})
    assert [r["question"] for r in lines(tmp_path)] == ["¿Cuántas jornadas hay?"]


def test_disabled_writes_nothing(tmp_path: Path) -> None:
    qlog = QuestionLog(tmp_path / "q", enabled=False)
    with app_client(qlog, [LLMResponse("ok", [])]) as c:
        assert c.post("/chat", json={"question": "hola"}).status_code == 200
    assert not (tmp_path / "q").exists()


def test_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QUESTION_LOG", "on")
    monkeypatch.setenv("QUESTION_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("QUESTION_LOG_RETENTION_DAYS", "7")
    monkeypatch.setenv("QUESTION_LOG_MAX_CHARS", "x")
    qlog = QuestionLog.from_env()
    assert qlog.enabled and qlog.folder == tmp_path and qlog.retention_days == 7 and qlog.max_chars == 500
    qlog.close()
    monkeypatch.setenv("QUESTION_LOG", "off")
    assert not QuestionLog.from_env().enabled


def test_daily_rotation_and_retention(tmp_path: Path) -> None:
    old, kept = tmp_path / "2026-10-01.jsonl", tmp_path / "2026-11-01.jsonl"
    for f in (old, kept, tmp_path / "notas.txt"):
        f.write_text("x\n", encoding="utf-8")
    qlog = QuestionLog(tmp_path, retention_days=30, enabled=False)
    qlog._write([(datetime(2026, 11, 11, 23, 59, 59, tzinfo=UTC), "ayer", None), (datetime(2026, 11, 12, 0, 0, 1, tzinfo=UTC), "hoy", None)])
    assert (tmp_path / "2026-11-11.jsonl").read_text(encoding="utf-8").count("ayer") == 1
    assert json.loads((tmp_path / "2026-11-12.jsonl").read_text(encoding="utf-8")) == {"ts": "2026-11-12T00:00:01Z", "question": "hoy"}
    assert not old.exists() and kept.exists() and (tmp_path / "notas.txt").exists()
    assert qlog._pruned == date(2026, 11, 12)


def test_write_failure_does_not_affect_agent(tmp_path: Path) -> None:
    blocker = tmp_path / "no-es-directorio"
    blocker.write_text("", encoding="utf-8")
    answers = []
    for qlog in (QuestionLog(tmp_path, enabled=False), QuestionLog(blocker / "questions")):  # el 2.º falla al crear el directorio
        with app_client(qlog, [LLMResponse("Cada mitad dura 20 minutos.", [])]) as c:
            res = c.post("/chat", json={"question": "¿Cuánto dura una mitad?"})
            answers.append((res.status_code, res.json()["answer"], res.json()["guardrail"]))
        assert qlog._thread is None  # el escritor sobrevive al fallo y se cierra limpio
    assert answers[0] == answers[1] and answers[0][0] == 200


def test_full_queue_drops_without_blocking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()
    qlog = QuestionLog(tmp_path)
    monkeypatch.setattr(qlog, "_write", lambda entries: gate.wait(5))  # disco «colgado»
    start = time.perf_counter()
    for i in range(3000):
        qlog.submit(f"p{i}")
    assert time.perf_counter() - start < 0.5 and qlog.dropped > 0
    gate.set()
    qlog.close()


def test_concurrent_questions(tmp_path: Path) -> None:
    qlog = QuestionLog(tmp_path)
    threads = [threading.Thread(target=lambda n=n: [qlog.submit(f"hilo{n}-{i}") for i in range(100)]) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    qlog.close()
    rows = lines(tmp_path)
    assert len(rows) == 800 and len({r["question"] for r in rows}) == 800


def test_answers_opt_in_same_as_shown_and_null_on_failure(tmp_path: Path) -> None:
    qlog = QuestionLog(tmp_path, answers=True, answer_max_chars=30)
    with app_client(qlog, [LLMResponse("Cada mitad dura 20 minutos.", []), LLMError("401 clave")]) as c:
        shown = c.post("/chat", json={"question": "¿Cuánto dura una mitad?"}).json()["answer"]
        assert c.post("/chat", json={"question": "otra"}).json().get("answer") is None  # fallo controlado del chat
    first, second = lines(tmp_path)
    assert first == {"ts": first["ts"], "question": "¿Cuánto dura una mitad?", "answer": " ".join(shown[:30].split())}
    assert second == {"ts": second["ts"], "question": "otra", "answer": None}


def test_answers_are_scrubbed(tmp_path: Path) -> None:
    qlog = QuestionLog(tmp_path, answers=True)
    qlog.submit("p", "Escribe a ana@x.com o al 612 345 678")
    qlog.close()
    assert lines(tmp_path)[0]["answer"] == "Escribe a [email] o al [tel]"


def test_answers_env_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QUESTION_LOG_ANSWERS", "on")
    monkeypatch.setenv("QUESTION_LOG_ANSWER_MAX_CHARS", "100")
    qlog = QuestionLog.from_env()
    assert qlog.answers and qlog.answer_max_chars == 100 and not qlog.enabled  # QUESTION_LOG sigue mandando
    monkeypatch.delenv("QUESTION_LOG_ANSWERS")
    assert not QuestionLog.from_env().answers


def test_ts_is_arrival_time(tmp_path: Path) -> None:
    qlog = QuestionLog(tmp_path, answers=True)
    qlog.submit("p", "r", datetime(2026, 11, 12, 8, 0, 0, tzinfo=UTC))
    qlog.close()
    assert lines(tmp_path) == [{"ts": "2026-11-12T08:00:00Z", "question": "p", "answer": "r"}]
