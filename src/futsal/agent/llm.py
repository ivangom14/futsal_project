"""Cliente LLM mínimo con tool calling. Tipos neutrales + adaptador Anthropic (httpx)."""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from futsal.config.settings import env_value


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    signature: str | None = None  # Gemini 3: `thoughtSignature`, hay que devolverla tal cual


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
class Usage:
    """Tokens de UNA llamada al modelo (no acumulados). `estimated`: contados por nosotros, no por el proveedor."""

    input_tokens: int = 0
    output_tokens: int = 0  # visibles (candidatos)
    thinking_tokens: int = 0  # Gemini 3: razonamiento interno; se factura como salida
    total_tokens: int | None = None  # el que informa el proveedor, si lo hace
    estimated: bool = False
    cached_input_tokens: int | None = None  # parte de la entrada servida desde caché del proveedor (suele facturarse menos)


@dataclass
class Attempt:
    """Un intento HTTP contra el proveedor. `waited_s`: espera aplicada ANTES del siguiente intento."""

    number: int
    status: int | None  # código HTTP; None si falló la red
    error: str | None  # tipo de error de red, si lo hubo
    waited_s: float = 0.0


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    usage: Usage | None = None
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def retries(self) -> int:
        return max(len(self.attempts) - 1, 0)


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


class LLMError(RuntimeError):
    def __init__(self, message: str, attempts: list[Attempt] | None = None) -> None:
        super().__init__(message)
        self.attempts: list[Attempt] = attempts or []


# --- reintentos controlados -------------------------------------------------------------------------
DEFAULT_MAX_RETRIES = 2  # conservador: como mucho 3 peticiones por llamada lógica
MAX_RETRIES_CAP = 5
DEFAULT_RETRY_MAX_WAIT = 20.0  # segundos; ninguna espera individual supera este valor


def _num_env(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(env_value(name, str(default)))
    except ValueError:
        return default
    return min(max(value, low), high)


@dataclass(frozen=True)
class RetryPolicy:
    """Solo errores TRANSITORIOS: 429, 5xx y fallos de red/timeout. Nunca 4xx permanentes ni respuestas inválidas."""

    max_retries: int = 0
    base_delay: float = 1.0  # espera exponencial: base, 2·base, 4·base… (si no hay Retry-After)
    max_wait: float = DEFAULT_RETRY_MAX_WAIT
    sleep: Callable[[float], None] = time.sleep

    @classmethod
    def from_env(cls) -> "RetryPolicy":
        retries = int(_num_env("LLM_MAX_RETRIES", DEFAULT_MAX_RETRIES, 0, MAX_RETRIES_CAP))
        return cls(retries, 1.0, _num_env("LLM_RETRY_MAX_WAIT", DEFAULT_RETRY_MAX_WAIT, 0, 120))


def _is_transient(status: int) -> bool:
    return status == 429 or 500 <= status <= 599


def _retry_after(resp: httpx.Response) -> float | None:
    """Segundos pedidos por el servidor: cabecera Retry-After o `retryDelay` del cuerpo de error de Gemini."""
    header = resp.headers.get("Retry-After", "").strip()
    if header.isdigit():
        return float(header)
    found = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', resp.text[:2000])
    return float(found.group(1)) if found else None


def post_with_retries(http: httpx.Client, path: str, body: dict[str, Any], headers: dict[str, str],
                      policy: RetryPolicy) -> tuple[httpx.Response, list[Attempt]]:
    """POST con reintentos acotados. Devuelve la respuesta final (la que el llamador evalúa) y TODOS los intentos."""
    attempts: list[Attempt] = []
    for number in range(1, policy.max_retries + 2):
        resp: httpx.Response | None = None
        try:
            resp = http.post(path, json=body, headers=headers)
            status: int | None = resp.status_code
            error = None
            transient = _is_transient(resp.status_code)
        except httpx.TransportError as exc:  # red, DNS, timeouts: transitorio
            status, error, transient = None, type(exc).__name__, True
        except httpx.HTTPError as exc:  # URL inválida, etc.: permanente
            attempts.append(Attempt(number, None, type(exc).__name__))
            raise LLMError(f"fallo de red con el LLM: {type(exc).__name__}", attempts) from exc
        attempt = Attempt(number, status, error)
        attempts.append(attempt)
        if not transient:
            return resp, attempts  # type: ignore[return-value]  # no transitorio: nunca es None aquí
        if number > policy.max_retries:
            break
        asked = _retry_after(resp) if resp is not None else None
        if asked is not None and asked > policy.max_wait:
            raise LLMError(f"LLM HTTP {status}: el servidor pide esperar {asked:.0f}s, por encima del máximo "
                           f"permitido ({policy.max_wait:.0f}s); no se reintenta", attempts)
        attempt.waited_s = min(asked if asked is not None else policy.base_delay * 2 ** (number - 1),
                               policy.max_wait)
        policy.sleep(attempt.waited_s)
    if resp is None:
        raise LLMError(f"fallo de red con el LLM: {attempts[-1].error} (tras {len(attempts)} intentos)", attempts)
    return resp, attempts


class LLMClient(Protocol):
    def complete(self, system: str, messages: list[Message],
                 tools: list[dict[str, Any]]) -> LLMResponse:
        """`tools`: [{name, description, input_schema}] (JSON Schema)."""
        ...


class AnthropicLLM:
    """Messages API de Anthropic vía httpx. Credenciales solo por entorno."""

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.anthropic.com",
                 max_tokens: int = 1024, client: httpx.Client | None = None,
                 retry: RetryPolicy | None = None) -> None:
        self._key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._http = client or httpx.Client(base_url=base_url, timeout=60)
        self._retry = retry or RetryPolicy()
        self.provider, self.model = "anthropic", model
        self.usage = {"input_tokens": 0, "output_tokens": 0}  # ACUMULADO de la instancia (todas las llamadas)

    @classmethod
    def from_env(cls) -> "AnthropicLLM":
        key = env_value("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY no definida (ver .env.example)")
        return cls(key, env_value("LLM_MODEL", "claude-haiku-4-5-20251001"),
                   env_value("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/"),
                   retry=RetryPolicy.from_env())

    def complete(self, system: str, messages: list[Message],
                 tools: list[dict[str, Any]]) -> LLMResponse:
        body = {"model": self._model, "max_tokens": self._max_tokens, "system": system,
                "tools": tools, "messages": [_to_wire(m) for m in messages]}
        resp, attempts = post_with_retries(self._http, "/v1/messages", body, {
            "x-api-key": self._key, "anthropic-version": "2023-06-01"}, self._retry)
        if resp.status_code != 200:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:200].replace(self._key, '***')}", attempts)
        try:
            data = resp.json()
            raw = data.get("usage", {})
            cached = raw.get("cache_read_input_tokens")
            usage = Usage(int(raw.get("input_tokens", 0)), int(raw.get("output_tokens", 0)),
                          cached_input_tokens=int(cached) if cached is not None else None)
            blocks = data.get("content", [])
            text = "".join(b["text"] for b in blocks if b["type"] == "text")
            calls = [ToolCall(b["id"], b["name"], b.get("input", {})) for b in blocks if b["type"] == "tool_use"]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:  # respuesta inválida: no se reintenta
            raise LLMError(f"respuesta inválida del LLM ({type(exc).__name__})", attempts) from exc
        for k in self.usage:
            self.usage[k] += int(raw.get(k, 0))
        return LLMResponse(text, calls, usage, attempts)


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
                part: dict[str, Any] = {"functionCall": {"name": c.name, "args": c.arguments}}
                if c.signature:
                    part["thoughtSignature"] = c.signature
                mparts.append(part)
            out.append({"role": "model", "parts": mparts})
        else:
            out.append({"role": "user", "parts": [{"text": m.text}]})
    return out


class GeminiLLM:
    """generateContent de Google Gemini vía httpx. La clave solo viaja en cabecera, nunca en URL."""

    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://generativelanguage.googleapis.com",
                 max_tokens: int = 1024, client: httpx.Client | None = None,
                 retry: RetryPolicy | None = None) -> None:
        self._key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._http = client or httpx.Client(base_url=base_url, timeout=60)
        self._retry = retry or RetryPolicy()
        self.provider, self.model = "gemini", model
        self.usage = {"input_tokens": 0, "output_tokens": 0}  # ACUMULADO de la instancia (todas las llamadas)

    @classmethod
    def from_env(cls) -> "GeminiLLM":
        key = env_value("GEMINI_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY no definida (ver .env.example)")
        return cls(key, env_value("GEMINI_MODEL", DEFAULT_GEMINI_MODEL), retry=RetryPolicy.from_env())

    def complete(self, system: str, messages: list[Message],
                 tools: list[dict[str, Any]]) -> LLMResponse:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": _gemini_contents(messages),
            "generationConfig": {"maxOutputTokens": self._max_tokens}}
        if tools:
            body["tools"] = _gemini_tools(tools)
        resp, attempts = post_with_retries(self._http, f"/v1beta/models/{self._model}:generateContent", body,
                                           {"x-goog-api-key": self._key}, self._retry)
        if resp.status_code != 200:
            detail = resp.text[:200].replace(self._key, "***")
            raise LLMError(f"LLM HTTP {resp.status_code}: {detail}", attempts)
        try:
            data = resp.json()
            meta = data.get("usageMetadata", {})
            total = meta.get("totalTokenCount")
            cached = meta.get("cachedContentTokenCount")
            usage = Usage(int(meta.get("promptTokenCount", 0)), int(meta.get("candidatesTokenCount", 0)),
                          int(meta.get("thoughtsTokenCount", 0)), int(total) if total is not None else None,
                          cached_input_tokens=int(cached) if cached is not None else None)
            cands = data.get("candidates") or [{}]
            parts = (cands[0].get("content") or {}).get("parts", [])
            text = "".join(p["text"] for p in parts if "text" in p)
            calls = [ToolCall(f"{p['functionCall']['name']}:{i}", p["functionCall"]["name"],
                              dict(p["functionCall"].get("args") or {}), p.get("thoughtSignature"))
                     for i, p in enumerate(q for q in parts if "functionCall" in q)]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:  # respuesta inválida: no se reintenta
            raise LLMError(f"respuesta inválida del LLM ({type(exc).__name__})", attempts) from exc
        self.usage["input_tokens"] += usage.input_tokens
        self.usage["output_tokens"] += usage.output_tokens
        return LLMResponse(text, calls, usage, attempts)


def create_llm() -> LLMClient:
    """Adaptador según `LLM_PROVIDER` (`anthropic` por defecto, o `gemini`)."""
    provider = env_value("LLM_PROVIDER", "anthropic").lower()
    if provider == "anthropic":
        return AnthropicLLM.from_env()
    if provider == "gemini":
        return GeminiLLM.from_env()
    raise LLMError(f"LLM_PROVIDER desconocido: {provider!r} (anthropic|gemini)")
