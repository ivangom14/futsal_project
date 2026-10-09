"""Cliente LLM mínimo con tool calling. Tipos neutrales + adaptador Anthropic (httpx)."""

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from futsal.config.settings import env_value


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    """`user` (text), `assistant` (text y/o tool_calls) o `tool` (tool_results)."""

    role: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    def complete(self, system: str, messages: list[Message],
                 tools: list[dict[str, Any]]) -> LLMResponse:
        """`tools`: [{name, description, input_schema}] (JSON Schema)."""
        ...


class AnthropicLLM:
    """Messages API de Anthropic vía httpx. Credenciales solo por entorno."""

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.anthropic.com",
                 max_tokens: int = 1024, client: httpx.Client | None = None) -> None:
        self._key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._http = client or httpx.Client(base_url=base_url, timeout=60)
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    @classmethod
    def from_env(cls) -> "AnthropicLLM":
        key = env_value("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY no definida (ver .env.example)")
        return cls(key, env_value("LLM_MODEL", "claude-haiku-4-5-20251001"),
                   env_value("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/"))

    def complete(self, system: str, messages: list[Message],
                 tools: list[dict[str, Any]]) -> LLMResponse:
        body = {"model": self._model, "max_tokens": self._max_tokens, "system": system,
                "tools": tools, "messages": [_to_wire(m) for m in messages]}
        try:
            resp = self._http.post("/v1/messages", json=body, headers={
                "x-api-key": self._key, "anthropic-version": "2023-06-01"})
        except httpx.HTTPError as exc:
            raise LLMError(f"fallo de red con el LLM: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        for k in self.usage:
            self.usage[k] += int(data.get("usage", {}).get(k, 0))
        blocks = data.get("content", [])
        text = "".join(b["text"] for b in blocks if b["type"] == "text")
        calls = [ToolCall(b["id"], b["name"], b.get("input", {}))
                 for b in blocks if b["type"] == "tool_use"]
        return LLMResponse(text, calls)


def _to_wire(m: Message) -> dict[str, Any]:
    if m.role == "tool":
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content,
             "is_error": r.is_error} for r in m.tool_results]}
    if m.role == "assistant":
        blocks: list[dict[str, Any]] = [{"type": "text", "text": m.text}] if m.text else []
        blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                   for c in m.tool_calls]
        return {"role": "assistant", "content": blocks}
    return {"role": "user", "content": m.text}


_GEMINI_DROP = {"$schema", "additionalProperties", "title", "default", "$defs", "examples"}


def _gemini_schema(node: Any) -> Any:
    """JSON Schema -> subconjunto OpenAPI que acepta Gemini (elimina claves no soportadas)."""
    if isinstance(node, dict):
        return {k: _gemini_schema(v) for k, v in node.items() if k not in _GEMINI_DROP}
    if isinstance(node, list):
        return [_gemini_schema(v) for v in node]
    return node


def _gemini_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decls = []
    for t in tools:
        decl: dict[str, Any] = {"name": t["name"], "description": t.get("description", "")}
        schema = _gemini_schema(t.get("input_schema") or {})
        if schema.get("properties"):  # Gemini rechaza objetos sin propiedades
            decl["parameters"] = schema
        decls.append(decl)
    return [{"functionDeclarations": decls}]


def _gemini_contents(messages: list[Message]) -> list[dict[str, Any]]:
    names: dict[str, str] = {}  # call_id -> nombre (functionResponse exige el nombre)
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            rparts: list[dict[str, Any]] = [{"functionResponse": {
                "name": names.get(r.call_id, r.call_id),
                "response": {"error": r.content} if r.is_error else {"result": r.content}}}
                for r in m.tool_results]
            out.append({"role": "user", "parts": rparts})
        elif m.role == "assistant":
            mparts: list[dict[str, Any]] = [{"text": m.text}] if m.text else []
            for c in m.tool_calls:
                names[c.id] = c.name
                mparts.append({"functionCall": {"name": c.name, "args": c.arguments}})
            out.append({"role": "model", "parts": mparts})
        else:
            out.append({"role": "user", "parts": [{"text": m.text}]})
    return out


class GeminiLLM:
    """generateContent de Google Gemini vía httpx. La clave solo viaja en cabecera, nunca en URL."""

    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://generativelanguage.googleapis.com",
                 max_tokens: int = 1024, client: httpx.Client | None = None) -> None:
        self._key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._http = client or httpx.Client(base_url=base_url, timeout=60)
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    @classmethod
    def from_env(cls) -> "GeminiLLM":
        key = env_value("GEMINI_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY no definida (ver .env.example)")
        return cls(key, env_value("GEMINI_MODEL", DEFAULT_GEMINI_MODEL))

    def complete(self, system: str, messages: list[Message],
                 tools: list[dict[str, Any]]) -> LLMResponse:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": _gemini_contents(messages),
            "generationConfig": {"maxOutputTokens": self._max_tokens}}
        if tools:
            body["tools"] = _gemini_tools(tools)
        try:
            resp = self._http.post(f"/v1beta/models/{self._model}:generateContent", json=body,
                                   headers={"x-goog-api-key": self._key})
        except httpx.HTTPError as exc:
            raise LLMError(f"fallo de red con el LLM: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            detail = resp.text[:200].replace(self._key, "***")
            raise LLMError(f"LLM HTTP {resp.status_code}: {detail}")
        data = resp.json()
        meta = data.get("usageMetadata", {})
        self.usage["input_tokens"] += int(meta.get("promptTokenCount", 0))
        self.usage["output_tokens"] += int(meta.get("candidatesTokenCount", 0))
        cands = data.get("candidates") or [{}]
        parts = (cands[0].get("content") or {}).get("parts", [])
        text = "".join(p["text"] for p in parts if "text" in p)
        calls = [ToolCall(f"{p['functionCall']['name']}:{i}", p["functionCall"]["name"],
                          dict(p["functionCall"].get("args") or {}))
                 for i, p in enumerate(q for q in parts if "functionCall" in q)]
        return LLMResponse(text, calls)


def create_llm() -> LLMClient:
    """Adaptador según `LLM_PROVIDER` (`anthropic` por defecto, o `gemini`)."""
    provider = env_value("LLM_PROVIDER", "anthropic").lower()
    if provider == "anthropic":
        return AnthropicLLM.from_env()
    if provider == "gemini":
        return GeminiLLM.from_env()
    raise LLMError(f"LLM_PROVIDER desconocido: {provider!r} (anthropic|gemini)")
