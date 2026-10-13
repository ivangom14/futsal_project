"""Reintentos controlados del cliente LLM: solo errores transitorios, acotados y registrados. Sin red ni esperas reales."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from futsal.agent.agent import Agent
from futsal.agent.llm import AnthropicLLM, GeminiLLM, LLMError, Message, RetryPolicy
from futsal.agent.mcp_client import SessionMcpClient
from futsal.mcp_server.server import create_server

KEY = "fake-test-key-not-real"
OK = {"candidates": [{"content": {"parts": [{"text": "hola"}]}}],
      "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2, "totalTokenCount": 12}}


def _llm(replies: list[Any], sleeps: list[float], retries: int = 2, max_wait: float = 20.0
         ) -> tuple[GeminiLLM, list[int]]:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        item = replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)

    client = httpx.Client(base_url="http://llm", transport=httpx.MockTransport(handler))
    policy = RetryPolicy(retries, 1.0, max_wait, sleep=sleeps.append)
    return GeminiLLM(KEY, "m", client=client, retry=policy), calls


def _complete(llm: GeminiLLM) -> Any:
    return llm.complete("s", [Message("user", "q")], [])


def test_transient_503_then_success_is_retried_and_recorded() -> None:
    sleeps: list[float] = []
    llm, calls = _llm([httpx.Response(503, text="overloaded"), OK], sleeps)
    resp = _complete(llm)
    assert resp.text == "hola" and len(calls) == 2 and resp.retries == 1
    assert [(a.number, a.status) for a in resp.attempts] == [(1, 503), (2, 200)]
    assert sleeps == [1.0] and resp.attempts[0].waited_s == 1.0


def test_exponential_backoff_without_retry_after() -> None:
    sleeps: list[float] = []
    llm, _ = _llm([httpx.Response(500), httpx.Response(502), OK], sleeps)
    assert _complete(llm).retries == 2 and sleeps == [1.0, 2.0]


def test_retry_after_header_is_respected_and_capped() -> None:
    sleeps: list[float] = []
    llm, _ = _llm([httpx.Response(429, headers={"Retry-After": "7"}), OK], sleeps)
    assert _complete(llm).retries == 1 and sleeps == [7.0]


def test_retry_delay_in_gemini_error_body_is_respected() -> None:
    sleeps: list[float] = []
    body = json.dumps({"error": {"details": [{"retryDelay": "4s"}]}})
    llm, _ = _llm([httpx.Response(429, text=body), OK], sleeps)
    _complete(llm)
    assert sleeps == [4.0]


def test_retry_after_above_max_wait_stops_without_waiting() -> None:
    sleeps: list[float] = []
    llm, calls = _llm([httpx.Response(429, headers={"Retry-After": "120"}), OK], sleeps, max_wait=20)
    with pytest.raises(LLMError, match="no se reintenta") as exc:
        _complete(llm)
    assert sleeps == [] and len(calls) == 1 and len(exc.value.attempts) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_errors_are_not_retried(status: int) -> None:
    sleeps: list[float] = []
    llm, calls = _llm([httpx.Response(status, text="no"), OK], sleeps)
    with pytest.raises(LLMError, match=f"HTTP {status}"):
        _complete(llm)
    assert len(calls) == 1 and sleeps == []


def test_invalid_response_is_not_retried() -> None:
    sleeps: list[float] = []
    llm, calls = _llm([httpx.Response(200, text="esto no es json"), OK], sleeps)
    with pytest.raises(LLMError, match="respuesta inválida"):
        _complete(llm)
    assert len(calls) == 1 and sleeps == []


def test_network_error_is_retried_then_succeeds() -> None:
    sleeps: list[float] = []
    llm, _ = _llm([httpx.ConnectError("boom"), httpx.ReadTimeout("slow"), OK], sleeps)
    resp = _complete(llm)
    assert resp.retries == 2 and [a.error for a in resp.attempts] == ["ConnectError", "ReadTimeout", None]


def test_exhausted_retries_raise_with_all_attempts_and_no_secret() -> None:
    sleeps: list[float] = []
    llm, calls = _llm([httpx.Response(503, text=f"fallo {KEY}")] * 3, sleeps, retries=2)
    with pytest.raises(LLMError) as exc:
        _complete(llm)
    assert len(calls) == 3 and len(exc.value.attempts) == 3 and KEY not in str(exc.value)
    assert sleeps == [1.0, 2.0]  # no hay espera tras el último intento


def test_network_exhaustion_message_and_attempts() -> None:
    llm, _ = _llm([httpx.ConnectError("x")] * 2, [], retries=1)
    with pytest.raises(LLMError, match="tras 2 intentos") as exc:
        _complete(llm)
    assert [a.error for a in exc.value.attempts] == ["ConnectError", "ConnectError"]


def test_retries_disabled_by_default_when_constructed_directly_and_by_zero() -> None:
    sleeps: list[float] = []
    llm, calls = _llm([httpx.Response(503), OK], sleeps, retries=0)
    with pytest.raises(LLMError):
        _complete(llm)
    assert len(calls) == 1
    direct = GeminiLLM(KEY, "m", client=httpx.Client(base_url="http://llm", transport=httpx.MockTransport(
        lambda r: httpx.Response(503))))
    with pytest.raises(LLMError) as exc:
        _complete(direct)
    assert len(exc.value.attempts) == 1


def test_policy_from_env_is_conservative_and_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("LLM_MAX_RETRIES", "LLM_RETRY_MAX_WAIT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir("/tmp")  # sin .env local
    p = RetryPolicy.from_env()
    assert (p.max_retries, p.max_wait) == (2, 20.0)
    monkeypatch.setenv("LLM_MAX_RETRIES", "99")
    monkeypatch.setenv("LLM_RETRY_MAX_WAIT", "9999")
    p = RetryPolicy.from_env()
    assert (p.max_retries, p.max_wait) == (5, 120.0)
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")
    assert RetryPolicy.from_env().max_retries == 0
    monkeypatch.setenv("LLM_MAX_RETRIES", "abc")
    assert RetryPolicy.from_env().max_retries == 2


def test_anthropic_adapter_shares_the_same_policy() -> None:
    sleeps: list[float] = []
    seq: list[httpx.Response] = [httpx.Response(529, text="overloaded"),
                                 httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}],
                                                           "usage": {"input_tokens": 3, "output_tokens": 1}})]
    client = httpx.Client(base_url="http://a", transport=httpx.MockTransport(lambda r: seq.pop(0)))
    llm = AnthropicLLM(KEY, "m", client=client, retry=RetryPolicy(1, 0.5, 5, sleep=sleeps.append))
    resp = llm.complete("s", [Message("user", "q")], [])
    assert resp.retries == 1 and sleeps == [0.5] and resp.usage is not None and resp.usage.input_tokens == 3


def _run_agent(llm: Any, path: Path) -> Any:
    server = create_server(httpx.Client(base_url="http://api", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"items": [], "count": 0}))))

    async def go() -> Any:
        async with connect(server._mcp_server) as session:
            return await Agent(llm, SessionMcpClient(session), 5, preload_context=False, trace_path=path).run("q")

    return asyncio.run(go())


def test_trace_records_retries_attempts_and_token_source(tmp_path: Path) -> None:
    sleeps: list[float] = []
    llm, _ = _llm([httpx.Response(503), OK], sleeps)
    path = tmp_path / "t.jsonl"
    res = _run_agent(llm, path)
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    call = row["llm_calls"][0]
    assert call["retries"] == 1 and [a["status"] for a in call["attempts"]] == [503, 200]
    assert call["retry_wait_ms"] == 1000.0 and call["token_source"] == "provider"
    assert row["totals"]["llm_retries"] == 1 and row["totals"]["token_source"] == "provider"
    assert res.run.totals()["llm_retries"] == 1


def test_trace_records_failed_attempts_when_retries_exhausted(tmp_path: Path) -> None:
    llm, _ = _llm([httpx.Response(503)] * 3, [], retries=2)
    path = tmp_path / "t.jsonl"
    res = _run_agent(llm, path)
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert res.error and row["llm_calls"][0]["retries"] == 2 and row["llm_calls"][0]["tokens_estimated"] is False
    assert row["totals"]["calls_without_usage"] == 1 and row["totals"]["token_source"] == "unreported"


