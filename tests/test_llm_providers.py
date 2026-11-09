"""Pasarela de modelos (T04): adaptadores Gemini/Anthropic/OpenAI con HTTP simulado, `create_llm(task)`, prueba de oro de Gemini y coste."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from futsal.agent.llm import (
    AnthropicLLM,
    GeminiLLM,
    LLMError,
    LLMQuotaError,
    Message,
    OpenAILLM,
    RetryPolicy,
    ToolCall,
    ToolResult,
    Usage,
    create_llm,
    estimate_cost,
)
from futsal.agent.trace import LlmCallRecord

KEY = "fake-test-key-not-real"
TOOLS = [{"name": "get_team", "description": "d", "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}}}},
         {"name": "get_group", "description": "g", "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}}}}]
GOLDEN = Path(__file__).parent / "fixtures" / "gemini_request_golden.json"

GEMINI_TEXT = {"candidates": [{"content": {"parts": [{"text": "hola"}]}}],
               "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2, "thoughtsTokenCount": 3, "totalTokenCount": 15}}
GEMINI_CALLS = {"candidates": [{"content": {"parts": [
    {"functionCall": {"name": "get_team", "args": {"id": 1}}, "thoughtSignature": "sig"},
    {"functionCall": {"name": "get_group", "args": {"id": 2}}}]}}]}
ANTHROPIC_TEXT = {"content": [{"type": "text", "text": "hola"}], "usage": {"input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 4}}
ANTHROPIC_CALLS = {"content": [{"type": "tool_use", "id": "tu1", "name": "get_team", "input": {"id": 1}},
                               {"type": "tool_use", "id": "tu2", "name": "get_group", "input": {"id": 2}}],
                   "usage": {"input_tokens": 10, "output_tokens": 2}}
REASONING = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "ENC"}
OPENAI_TEXT = {"output": [REASONING, {"type": "message", "content": [{"type": "output_text", "text": "ho"}, {"type": "output_text", "text": "la"}]}],
               "usage": {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 40}, "output_tokens": 30,
                         "output_tokens_details": {"reasoning_tokens": 25}, "total_tokens": 130}}
OPENAI_CALLS = {"output": [REASONING,
                           {"type": "function_call", "call_id": "c1", "name": "get_team", "arguments": "{\"id\": 1}"},
                           {"type": "function_call", "call_id": "c2", "name": "get_group", "arguments": "{\"id\": 2}"}],
                "usage": {"input_tokens": 5, "output_tokens": 5}}


def _make(kind: str, replies: list[Any], sleeps: list[float] | None = None, **kw: Any) -> tuple[Any, list[dict[str, Any]], list[httpx.Request]]:
    bodies: list[dict[str, Any]] = []
    reqs: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reqs.append(request)
        bodies.append(json.loads(request.content))
        item = replies.pop(0)
        return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)

    client = httpx.Client(base_url="http://llm", transport=httpx.MockTransport(handler))
    policy = RetryPolicy(2, 1.0, 20.0, sleep=(sleeps if sleeps is not None else []).append)
    cls = {"gemini": GeminiLLM, "anthropic": AnthropicLLM, "openai": OpenAILLM}[kind]
    return cls(KEY, "m", client=client, retry=policy, **kw), bodies, reqs


ALL = ["gemini", "anthropic", "openai"]
TEXT = {"gemini": GEMINI_TEXT, "anthropic": ANTHROPIC_TEXT, "openai": OPENAI_TEXT}
CALLS = {"gemini": GEMINI_CALLS, "anthropic": ANTHROPIC_CALLS, "openai": OPENAI_CALLS}


@pytest.mark.parametrize("kind", ALL)
def test_text_response(kind: str) -> None:
    llm, _, _ = _make(kind, [TEXT[kind]])
    resp = llm.complete("s", [Message("user", "q")], [])
    assert resp.text == "hola" and resp.tool_calls == [] and resp.usage is not None
    assert llm.provider == kind and llm.model == "m"
    assert llm.usage["input_tokens"] == resp.usage.input_tokens


@pytest.mark.parametrize("kind", ALL)
def test_parallel_function_calls_in_one_response(kind: str) -> None:
    llm, _, _ = _make(kind, [CALLS[kind]])
    resp = llm.complete("s", [Message("user", "q")], TOOLS)
    assert [(c.name, c.arguments) for c in resp.tool_calls] == [("get_team", {"id": 1}), ("get_group", {"id": 2})]
    assert len({c.id for c in resp.tool_calls}) == 2


def test_single_function_call_openai() -> None:
    reply = {"output": [{"type": "function_call", "call_id": "c1", "name": "get_team", "arguments": "{\"id\": 7}"}]}
    llm, _, _ = _make("openai", [reply])
    resp = llm.complete("s", [Message("user", "q")], TOOLS)
    assert resp.text == "" and resp.tool_calls == [ToolCall("c1", "get_team", {"id": 7}, None)]
    assert resp.usage == Usage()


def test_openai_request_shape_and_headers() -> None:
    llm, bodies, reqs = _make("openai", [OPENAI_TEXT])
    llm.complete("SYS", [Message("user", "q")], TOOLS)
    b = bodies[0]
    assert reqs[0].url.path == "/v1/responses" and reqs[0].headers["Authorization"] == f"Bearer {KEY}"
    assert b["instructions"] == "SYS" and b["store"] is False and b["include"] == ["reasoning.encrypted_content"]
    assert b["max_output_tokens"] == 1024 and "reasoning" not in b
    assert b["input"] == [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "q"}]}]
    assert b["tools"][0] == {"type": "function", "name": "get_team", "description": "d", "parameters": TOOLS[0]["input_schema"]}
    llm2, bodies2, _ = _make("openai", [OPENAI_TEXT], reasoning_effort="low")
    llm2.complete("s", [Message("user", "q")], [])
    assert bodies2[0]["reasoning"] == {"effort": "low"} and "tools" not in bodies2[0]


def test_openai_usage_normalization() -> None:
    llm, _, _ = _make("openai", [OPENAI_TEXT])
    u = llm.complete("s", [Message("user", "q")], []).usage
    assert u == Usage(100, 5, 25, 130, cached_input_tokens=40)  # visibles = 30 - 25
    assert llm.usage == {"input_tokens": 100, "output_tokens": 5}


def test_openai_round_trip_resends_reasoning_and_function_items() -> None:
    llm, bodies, _ = _make("openai", [OPENAI_CALLS, OPENAI_TEXT])
    first = llm.complete("s", [Message("user", "q")], TOOLS)
    msgs = [Message("user", "q"), Message("assistant", "", first.tool_calls),
            Message("tool", tool_results=[ToolResult("c1", "r1"), ToolResult("c2", "boom", True)])]
    final = llm.complete("s", msgs, TOOLS)
    assert final.text == "hola"
    assert bodies[1]["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "q"}]},
        REASONING,
        {"type": "function_call", "call_id": "c1", "name": "get_team", "arguments": "{\"id\": 1}"},
        {"type": "function_call", "call_id": "c2", "name": "get_group", "arguments": "{\"id\": 2}"},
        {"type": "function_call_output", "call_id": "c1", "output": "r1"},
        {"type": "function_call_output", "call_id": "c2", "output": "boom"}]


def test_openai_assistant_text_item() -> None:
    llm, bodies, _ = _make("openai", [OPENAI_TEXT])
    llm.complete("s", [Message("user", "q"), Message("assistant", "resp"), Message("user", "otra")], [])
    assert bodies[0]["input"][1] == {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "resp"}]}


def test_gemini_and_anthropic_round_trip() -> None:
    g, gb, _ = _make("gemini", [GEMINI_CALLS, GEMINI_TEXT])
    first = g.complete("s", [Message("user", "q")], TOOLS)
    g.complete("s", [Message("user", "q"), Message("assistant", "", first.tool_calls),
                     Message("tool", tool_results=[ToolResult(c.id, "r") for c in first.tool_calls])], TOOLS)
    parts = gb[1]["contents"][1]["parts"]
    assert parts[0]["thoughtSignature"] == "sig" and parts[1]["functionCall"]["name"] == "get_group"
    assert gb[1]["contents"][2]["parts"][0]["functionResponse"]["name"] == "get_team"
    a, ab, _ = _make("anthropic", [ANTHROPIC_CALLS, ANTHROPIC_TEXT])
    first = a.complete("s", [Message("user", "q")], TOOLS)
    a.complete("s", [Message("user", "q"), Message("assistant", "", first.tool_calls),
                     Message("tool", tool_results=[ToolResult("tu1", "r")])], TOOLS)
    assert ab[1]["messages"][1]["content"][0] == {"type": "tool_use", "id": "tu1", "name": "get_team", "input": {"id": 1}}
    assert ab[1]["messages"][2]["content"][0]["tool_use_id"] == "tu1"


@pytest.mark.parametrize("kind,ok", [("gemini", GEMINI_TEXT), ("anthropic", ANTHROPIC_TEXT), ("openai", OPENAI_TEXT)])
def test_429_is_retried_then_succeeds(kind: str, ok: dict[str, Any]) -> None:
    sleeps: list[float] = []
    llm, bodies, _ = _make(kind, [httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}}, headers={"Retry-After": "3"}), ok], sleeps)
    resp = llm.complete("s", [Message("user", "q")], [])
    assert resp.retries == 1 and sleeps == [3.0] and len(bodies) == 2


def test_openai_insufficient_quota_is_not_retried() -> None:
    sleeps: list[float] = []
    reply = httpx.Response(429, json={"error": {"code": "insufficient_quota", "message": f"key {KEY} out"}})
    llm, bodies, _ = _make("openai", [reply, OPENAI_TEXT], sleeps)
    with pytest.raises(LLMQuotaError) as ei:
        llm.complete("s", [Message("user", "q")], [])
    assert len(bodies) == 1 and sleeps == [] and len(ei.value.attempts) == 1 and KEY not in str(ei.value)


@pytest.mark.parametrize("kind", ALL)
def test_5xx_is_retried_and_finally_fails(kind: str) -> None:
    sleeps: list[float] = []
    llm, bodies, _ = _make(kind, [httpx.Response(503, text="down") for _ in range(3)], sleeps)
    with pytest.raises(LLMError) as ei:
        llm.complete("s", [Message("user", "q")], [])
    assert len(bodies) == 3 and len(ei.value.attempts) == 3 and not isinstance(ei.value, LLMQuotaError)


@pytest.mark.parametrize("kind", ALL)
def test_4xx_is_not_retried_and_hides_key(kind: str) -> None:
    llm, bodies, _ = _make(kind, [httpx.Response(401, text=f"bad key {KEY}")])
    with pytest.raises(LLMError) as ei:
        llm.complete("s", [Message("user", "q")], [])
    assert len(bodies) == 1 and KEY not in str(ei.value) and "401" in str(ei.value)


@pytest.mark.parametrize("kind", ALL)
def test_invalid_json_is_invalid_response_without_retry(kind: str) -> None:
    llm, bodies, _ = _make(kind, [httpx.Response(200, text="<html>no json</html>")])
    with pytest.raises(LLMError, match="respuesta inválida"):
        llm.complete("s", [Message("user", "q")], [])
    assert len(bodies) == 1


@pytest.mark.parametrize("arguments", ["{no json", "[1, 2]"])
def test_openai_invalid_arguments(arguments: str) -> None:
    reply = {"output": [{"type": "function_call", "call_id": "c1", "name": "get_team", "arguments": arguments}]}
    llm, _, _ = _make("openai", [reply])
    with pytest.raises(LLMError, match="respuesta inválida"):
        llm.complete("s", [Message("user", "q")], TOOLS)


# --- Gemini: prueba de oro y thinkingConfig -----------------------------------------------------------
def _golden_bodies(**kw: Any) -> list[str]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    llm = GeminiLLM("k", "gemini-x", client=httpx.Client(base_url="http://llm", transport=httpx.MockTransport(handler)), **kw)
    tools = [{"name": "get_team", "description": "d", "input_schema": {"type": "object", "properties": {"id": {"type": "integer", "title": "T"}}, "additionalProperties": False}}]
    msgs = [Message("user", "hola"), Message("assistant", "", [ToolCall("get_team:0", "get_team", {"id": 3}, "sig")]),
            Message("tool", tool_results=[ToolResult("get_team:0", "{\"a\":1}")]), Message("user", "y?")]
    llm.complete("sys", msgs, tools)
    llm.complete("sys", [Message("user", "q")], [])
    return seen


def test_gemini_request_body_is_byte_identical_to_golden() -> None:
    assert _golden_bodies() == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_gemini_thinking_level_only_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies = [json.loads(b) for b in _golden_bodies(thinking_level="low")]
    assert all(b["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"} for b in bodies)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.delenv("GEMINI_THINKING_LEVEL", raising=False)
    assert GeminiLLM.from_env()._thinking_level is None
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "high")
    assert GeminiLLM.from_env()._thinking_level == "high"


# --- Anthropic: modelo y cache_control ----------------------------------------------------------------
def test_anthropic_default_model_and_cached_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert AnthropicLLM.from_env().model == "claude-haiku-5-5"
    llm, _, _ = _make("anthropic", [ANTHROPIC_TEXT])
    assert llm.complete("s", [Message("user", "q")], []).usage.cached_input_tokens == 4  # type: ignore[union-attr]


def test_anthropic_cache_control_is_opt_in() -> None:
    plain, pb, _ = _make("anthropic", [ANTHROPIC_TEXT])
    plain.complete("SYS", [Message("user", "q")], TOOLS)
    assert pb[0]["system"] == "SYS" and "cache_control" not in json.dumps(pb[0])
    cached, cb, _ = _make("anthropic", [ANTHROPIC_TEXT], prompt_cache=True)
    cached.complete("SYS", [Message("user", "q")], TOOLS)
    eph = {"type": "ephemeral"}
    assert cb[0]["system"] == [{"type": "text", "text": "SYS", "cache_control": eph}]
    assert "cache_control" not in cb[0]["tools"][0] and cb[0]["tools"][-1]["cache_control"] == eph
    assert "cache_control" not in TOOLS[-1]  # no muta las herramientas del llamador


# --- create_llm ----------------------------------------------------------------------------------------
@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    monkeypatch.chdir(tmp_path)  # sin .env local
    for var in ("LLM_PROVIDER", "LLM_MODEL", "GEMINI_MODEL", "OPENAI_MODEL", *(f"LLM_MODEL_{t}" for t in ("ROUTER", "INTERPRETER", "SIMPLE", "COMPLEX", "JUDGE"))):
        monkeypatch.delenv(var, raising=False)
    for var in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(var, KEY)
    return monkeypatch


def test_create_llm_without_task_is_unchanged(clean_env: pytest.MonkeyPatch) -> None:
    llm = create_llm()
    assert (llm.provider, llm.model) == ("anthropic", "claude-haiku-5-5")  # type: ignore[attr-defined]
    clean_env.setenv("LLM_PROVIDER", "gemini")
    assert create_llm().provider == "gemini"  # type: ignore[attr-defined]
    clean_env.setenv("LLM_PROVIDER", "openai")
    llm = create_llm()
    assert (llm.provider, llm.model) == ("openai", "gpt-6-luna")  # type: ignore[attr-defined]
    clean_env.setenv("LLM_PROVIDER", "otro")
    with pytest.raises(LLMError, match="desconocido"):
        create_llm()


def test_create_llm_task_override_and_fallback(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("LLM_PROVIDER", "gemini")
    clean_env.setenv("LLM_MODEL_SIMPLE", "openai:gpt-6.1-terra")
    clean_env.setenv("LLM_MODEL_COMPLEX", "anthropic:claude-sonnet-5-5")
    clean_env.setenv("LLM_MODEL_JUDGE", "gemini:gemini-4-argon")
    for task, expected in [("simple", ("openai", "gpt-6.1-terra")), ("complex", ("anthropic", "claude-sonnet-5-5")),
                           ("judge", ("gemini", "gemini-4-argon")), ("router", ("gemini", "gemini-3.5-flash-lite")), (None, ("gemini", "gemini-3.5-flash-lite"))]:
        llm = create_llm(task)
        assert (llm.provider, llm.model) == expected  # type: ignore[attr-defined]


@pytest.mark.parametrize("spec", ["gpt-6-luna", "openai:", ":gpt", "mistral:x"])
def test_create_llm_invalid_task_spec(clean_env: pytest.MonkeyPatch, spec: str) -> None:
    clean_env.setenv("LLM_MODEL_SIMPLE", spec)
    with pytest.raises(LLMError, match="LLM_MODEL_SIMPLE"):
        create_llm("simple")


def test_create_llm_unknown_task_and_missing_key(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(LLMError, match="tarea"):
        create_llm("nope")
    clean_env.delenv("OPENAI_API_KEY")
    clean_env.setenv("LLM_MODEL_SIMPLE", "openai:gpt-6-luna")
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        create_llm("simple")


# --- coste ----------------------------------------------------------------------------------------------
def test_estimate_cost_with_and_without_rate() -> None:
    u = Usage(1_000_000, 400_000, 100_000, cached_input_tokens=200_000)
    assert estimate_cost("openai", "gpt-6.1-sol", u) == pytest.approx(0.8 * 2.0 + 0.2 * 0.10 + 0.5 * 10.0)
    assert estimate_cost("gemini", "gemini-3.5-flash-lite", Usage(1000, 10, 0)) == pytest.approx((1000 * 0.30 + 10 * 2.5) / 1e6)
    # Anthropic: `input_tokens` no incluye las lecturas de caché
    assert estimate_cost("anthropic", "claude-haiku-5-5", Usage(1000, 100, 0, cached_input_tokens=500)) == pytest.approx((1000 * 0.10 + 500 * 0.01 + 100 * 0.50) / 1e6)
    assert estimate_cost("openai", "modelo-sin-tarifa", u) is None
    assert estimate_cost("openai", "gpt-6-luna", None) is None


def test_llm_call_record_cost_is_optional() -> None:
    rec = LlmCallRecord(1, 1.0, 1, 1, 0, 2, False, 0)
    assert rec.cost_usd is None
