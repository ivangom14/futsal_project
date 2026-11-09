"""Cliente LLM mínimo con tool calling. Tipos neutrales + adaptador Anthropic (httpx)."""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
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
    # Normalización entre proveedores: `output_tokens` = salida VISIBLE y `thinking_tokens` = razonamiento, siempre por separado.
    # Gemini ya los informa separados; OpenAI informa `output_tokens` INCLUYENDO el razonamiento y el adaptador lo descuenta.


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
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
DEFAULT_OPENAI_MODEL = "gpt-6-luna"


class LLMError(RuntimeError):
    def __init__(self, message: str, attempts: list[Attempt] | None = None) -> None:
        super().__init__(message)
        self.attempts: list[Attempt] = attempts or []


QUOTA_MARKER = "cuota del proveedor agotada"
QUOTA_WAIT_THRESHOLD_S = 600.0  # una espera pedida de 10 min o más no es de ritmo (por minuto): es una cuota diaria o de ventana larga; esperar no tiene sentido


class LLMQuotaError(LLMError):
    """El proveedor pide esperar mucho más que un límite por minuto (p. ej. horas): la cuota está agotada. Seguir llamando solo gasta peticiones inútiles."""

    def __init__(self, message: str, attempts: list[Attempt] | None = None, retry_after: float | None = None) -> None:
        super().__init__(message, attempts)
        self.retry_after = retry_after


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
                      policy: RetryPolicy, quota_check: Callable[[httpx.Response], str | None] | None = None
                      ) -> tuple[httpx.Response, list[Attempt]]:
    """POST con reintentos acotados. Devuelve la respuesta final (la que el llamador evalúa) y TODOS los intentos.
    `quota_check(resp)`: si devuelve un texto para una respuesta transitoria (429), es cuota agotada: `LLMQuotaError` sin reintentar."""
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
        if resp is not None and quota_check is not None:
            quota_msg = quota_check(resp)
            if quota_msg:
                raise LLMQuotaError(f"{QUOTA_MARKER}: LLM HTTP {status}: {quota_msg}", attempts)
        asked = _retry_after(resp) if resp is not None else None
        if asked is not None and asked >= QUOTA_WAIT_THRESHOLD_S:  # independiente de cuántos reintentos queden
            raise LLMQuotaError(f"{QUOTA_MARKER}: LLM HTTP {status}: el servidor pide esperar {asked:.0f}s (≈{asked / 3600:.1f} h), por encima del máximo "
                                f"permitido ({policy.max_wait:.0f}s); no se reintenta", attempts, asked)
        if number > policy.max_retries:
            break
        if asked is not None and asked > policy.max_wait:
            raise LLMError(f"LLM HTTP {status}: el servidor pide esperar {asked:.0f}s, por encima del máximo "
                           f"permitido ({policy.max_wait:.0f}s); no se reintenta", attempts)
        attempt.waited_s = min(asked if asked is not None else policy.base_delay * 2 ** (number - 1),
                               policy.max_wait)
        policy.sleep(attempt.waited_s)
    if resp is None:
        raise LLMError(f"fallo de red con el LLM: {attempts[-1].error} (tras {len(attempts)} intentos)", attempts)
    return resp, attempts


class PacedLLM:
    """Separa en el tiempo las llamadas de un cliente: al menos `min_interval` s entre el inicio de dos peticiones.
    Evita superar el límite por minuto del proveedor (429) en las evaluaciones largas. No cambia las respuestas."""

    def __init__(self, inner: "LLMClient", min_interval: float, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._inner, self._min, self._sleep, self._clock = inner, max(min_interval, 0.0), sleep, clock
        self._last: float | None = None
        self.provider = getattr(inner, "provider", "")
        self.model = getattr(inner, "model", "")
        self.waited_s = 0.0
        self.last_wait_s = 0.0  # pausa de la ÚLTIMA llamada: el agente la descuenta de la latencia

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> "LLMResponse":
        self.last_wait_s = 0.0
        if self._last is not None:
            wait = self._min - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
                self.waited_s += wait
                self.last_wait_s = wait
        self._last = self._clock()
        return self._inner.complete(system, messages, tools)


BUDGET_EXHAUSTED = "presupuesto de peticiones agotado"


class BudgetedLLM:
    """Tope DURO de peticiones HTTP al proveedor (llamadas + reintentos) para todo lo que pase por este cliente.

    Antes de cada llamada lógica comprueba que, en el PEOR caso (1 + reintentos permitidos), el tope no se superaría; si no, lanza `LLMError` SIN enviar nada.
    Tras la llamada suma las peticiones reales (los intentos). Así el consumo nunca supera `max_requests`, ni siquiera a mitad de un caso."""

    def __init__(self, inner: "LLMClient", max_requests: int, worst_case_per_call: int | None = None) -> None:
        self._inner, self.max_requests = inner, max(int(max_requests), 0)
        self._worst = max(worst_case_per_call if worst_case_per_call is not None else RetryPolicy.from_env().max_retries + 1, 1)
        self.used = 0  # peticiones realmente enviadas
        self.refused = 0  # llamadas lógicas rechazadas por falta de presupuesto (no enviaron nada)

    def __getattr__(self, name: str) -> Any:  # provider, model, usage…
        return getattr(self._inner, name)

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> "LLMResponse":
        if self.used + self._worst > self.max_requests:
            self.refused += 1
            raise LLMError(f"{BUDGET_EXHAUSTED}: {self.used} usadas de {self.max_requests} (una llamada necesita hasta {self._worst}); no se envía nada")
        try:
            resp = self._inner.complete(system, messages, tools)
        except LLMError as exc:
            self.used += max(len(exc.attempts), 1)
            raise
        except Exception:
            self.used += self._worst  # fallo desconocido: se cuenta lo peor
            raise
        self.used += max(len(resp.attempts), 1)
        return resp


class LLMClient(Protocol):
    def complete(self, system: str, messages: list[Message],
                 tools: list[dict[str, Any]]) -> LLMResponse:
        """`tools`: [{name, description, input_schema}] (JSON Schema)."""
        ...


class AnthropicLLM:
    """Messages API de Anthropic vía httpx. Credenciales solo por entorno."""

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.anthropic.com",
                 max_tokens: int = 1024, client: httpx.Client | None = None,
                 retry: RetryPolicy | None = None, prompt_cache: bool = False) -> None:
        self._key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._http = client or httpx.Client(base_url=base_url, timeout=60)
        self._retry = retry or RetryPolicy()
        self._prompt_cache = prompt_cache  # `ANTHROPIC_PROMPT_CACHE=on`: cache_control en el system y en la última herramienta
        self.provider, self.model = "anthropic", model
        self.usage = {"input_tokens": 0, "output_tokens": 0}  # ACUMULADO de la instancia (todas las llamadas)

    @classmethod
    def from_env(cls, model: str | None = None) -> "AnthropicLLM":
        key = env_value("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY no definida (ver .env.example)")
        return cls(key, model or env_value("LLM_MODEL", DEFAULT_ANTHROPIC_MODEL),
                   env_value("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/"),
                   retry=RetryPolicy.from_env(), prompt_cache=env_value("ANTHROPIC_PROMPT_CACHE").lower() == "on")

    def complete(self, system: str, messages: list[Message],
                 tools: list[dict[str, Any]]) -> LLMResponse:
        body = {"model": self._model, "max_tokens": self._max_tokens, "system": system,
                "tools": tools, "messages": [_to_wire(m) for m in messages]}
        if self._prompt_cache:
            cache = {"type": "ephemeral"}
            body["system"] = [{"type": "text", "text": system, "cache_control": cache}]
            if tools:
                body["tools"] = [*tools[:-1], {**tools[-1], "cache_control": cache}]
        if not tools:  # el intérprete del modo `llm` no usa herramientas (con herramientas el cuerpo es idéntico al de siempre)
            del body["tools"]
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
                 retry: RetryPolicy | None = None, thinking_level: str | None = None) -> None:
        self._key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._http = client or httpx.Client(base_url=base_url, timeout=60)
        self._retry = retry or RetryPolicy()
        self._thinking_level = thinking_level  # `GEMINI_THINKING_LEVEL`; None = la petición no lleva thinkingConfig
        self.provider, self.model = "gemini", model
        self.usage = {"input_tokens": 0, "output_tokens": 0}  # ACUMULADO de la instancia (todas las llamadas)

    @classmethod
    def from_env(cls, model: str | None = None) -> "GeminiLLM":
        key = env_value("GEMINI_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY no definida (ver .env.example)")
        return cls(key, model or env_value("GEMINI_MODEL", DEFAULT_GEMINI_MODEL), retry=RetryPolicy.from_env(),
                   thinking_level=env_value("GEMINI_THINKING_LEVEL") or None)

    def complete(self, system: str, messages: list[Message],
                 tools: list[dict[str, Any]]) -> LLMResponse:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": _gemini_contents(messages),
            "generationConfig": {"maxOutputTokens": self._max_tokens}}
        if self._thinking_level:
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": self._thinking_level}
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


# --- OpenAI (Responses API, sin estado) ------------------------------------------------------------
def _openai_input(messages: list[Message]) -> list[dict[str, Any]]:
    """Mensajes neutrales -> items de la Responses API. Los items `reasoning` que el modelo devolvió junto a sus llamadas de función
    viajan opacos (JSON) en `ToolCall.signature` de la primera llamada y se reenvían ANTES de ella (la petición es `store: false`)."""
    items: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            items += [{"type": "function_call_output", "call_id": r.call_id, "output": r.content} for r in m.tool_results]
        elif m.role == "assistant":
            for c in m.tool_calls:
                if c.signature:
                    items += json.loads(c.signature)
                    break
            if m.text:
                items.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": m.text}]})
            items += [{"type": "function_call", "call_id": c.id, "name": c.name, "arguments": json.dumps(c.arguments)}
                      for c in m.tool_calls]
        else:
            items.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": m.text}]})
    return items


def _openai_quota(resp: httpx.Response) -> str | None:
    if resp.status_code != 429:
        return None
    try:
        err = resp.json().get("error") or {}
    except (ValueError, AttributeError):
        return None
    return "insufficient_quota" if isinstance(err, dict) and err.get("code") == "insufficient_quota" else None


class OpenAILLM:
    """Responses API de OpenAI (`POST /v1/responses`) vía httpx, sin estado (`store: false`). La clave solo viaja en cabecera."""

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com",
                 max_tokens: int = 1024, client: httpx.Client | None = None,
                 retry: RetryPolicy | None = None, reasoning_effort: str | None = None) -> None:
        self._key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._http = client or httpx.Client(base_url=base_url, timeout=60)
        self._retry = retry or RetryPolicy()
        self._effort = reasoning_effort  # `OPENAI_REASONING_EFFORT`; None = no se envía `reasoning`
        self.provider, self.model = "openai", model
        self.usage = {"input_tokens": 0, "output_tokens": 0}  # ACUMULADO de la instancia (todas las llamadas)

    @classmethod
    def from_env(cls, model: str | None = None) -> "OpenAILLM":
        key = env_value("OPENAI_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY no definida (ver .env.example)")
        return cls(key, model or env_value("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
                   env_value("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/"),
                   retry=RetryPolicy.from_env(), reasoning_effort=env_value("OPENAI_REASONING_EFFORT") or None)

    def complete(self, system: str, messages: list[Message],
                 tools: list[dict[str, Any]]) -> LLMResponse:
        body: dict[str, Any] = {"model": self._model, "instructions": system, "input": _openai_input(messages),
                                "max_output_tokens": self._max_tokens, "store": False,
                                "include": ["reasoning.encrypted_content"]}
        if tools:
            body["tools"] = [{"type": "function", "name": t["name"], "description": t.get("description", ""),
                              "parameters": t.get("input_schema") or {"type": "object", "properties": {}}} for t in tools]
        if self._effort:
            body["reasoning"] = {"effort": self._effort}
        resp, attempts = post_with_retries(self._http, "/v1/responses", body, {"Authorization": f"Bearer {self._key}"},
                                           self._retry, _openai_quota)
        if resp.status_code != 200:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:200].replace(self._key, '***')}", attempts)
        try:
            data = resp.json()
            raw = data.get("usage") or {}
            cached = (raw.get("input_tokens_details") or {}).get("cached_tokens")
            reasoning = int((raw.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
            total = raw.get("total_tokens")
            out_total = int(raw.get("output_tokens", 0))
            # OpenAI: `output_tokens` YA incluye el razonamiento. Aquí `output_tokens` son los visibles (como Gemini, donde van separados).
            usage = Usage(int(raw.get("input_tokens", 0)), max(out_total - reasoning, 0), reasoning,
                          int(total) if total is not None else None,
                          cached_input_tokens=int(cached) if cached is not None else None)
            output = data.get("output") or []
            text = "".join(c["text"] for it in output if it.get("type") == "message"
                           for c in it.get("content") or [] if c.get("type") == "output_text")
            reasoning_items = [it for it in output if it.get("type") == "reasoning"]
            signature = json.dumps(reasoning_items) if reasoning_items else None
            calls: list[ToolCall] = []
            for it in (q for q in output if q.get("type") == "function_call"):
                args = json.loads(it.get("arguments") or "{}")
                if not isinstance(args, dict):
                    raise TypeError("arguments no es un objeto")
                calls.append(ToolCall(it["call_id"], it["name"], args, signature if not calls else None))
        except (ValueError, KeyError, TypeError, AttributeError) as exc:  # respuesta inválida: no se reintenta
            raise LLMError(f"respuesta inválida del LLM ({type(exc).__name__})", attempts) from exc
        self.usage["input_tokens"] += usage.input_tokens
        self.usage["output_tokens"] += usage.output_tokens
        return LLMResponse(text, calls, usage, attempts)


# --- tarifas y coste --------------------------------------------------------------------------------
PRICES_FILE = Path(__file__).resolve().parent.parent / "config" / "llm_prices.json"


@lru_cache(maxsize=1)
def _load_prices() -> dict[str, Any]:
    try:
        models = json.loads(PRICES_FILE.read_text(encoding="utf-8")).get("models", {})
        return models if isinstance(models, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def estimate_cost(provider: str, model: str, usage: Usage | None) -> float | None:
    """Coste en USD de UNA llamada según `config/llm_prices.json`; `None` sin tarifa o sin usage (nunca se inventa).
    entrada no cacheada × input + cacheada × cached_input + (salida + razonamiento) × salida. Precios por 1M de tokens."""
    rate = _load_prices().get(f"{provider}:{model}")
    if usage is None or not isinstance(rate, dict):
        return None
    try:
        cached = usage.cached_input_tokens or 0
        # Anthropic informa `input_tokens` SIN las lecturas de caché (van aparte); Gemini y OpenAI las incluyen en la entrada.
        uncached = usage.input_tokens if provider == "anthropic" else max(usage.input_tokens - cached, 0)
        in_rate, out_rate = float(rate["input"]), float(rate["output"])
        cached_rate = float(rate["cached_input"]) if rate.get("cached_input") is not None else in_rate
        return (uncached * in_rate + cached * cached_rate + (usage.output_tokens + usage.thinking_tokens) * out_rate) / 1_000_000
    except (KeyError, TypeError):
        return None


# --- fábrica ----------------------------------------------------------------------------------------
LLM_PROVIDERS = ("anthropic", "gemini", "openai")
LLM_TASKS = ("router", "interpreter", "simple", "complex", "judge")


def _build(provider: str, model: str | None = None) -> LLMClient:
    if provider == "anthropic":
        return AnthropicLLM.from_env(model)
    if provider == "gemini":
        return GeminiLLM.from_env(model)
    if provider == "openai":
        return OpenAILLM.from_env(model)
    raise LLMError(f"LLM_PROVIDER desconocido: {provider!r} (anthropic|gemini|openai)")


def create_llm(task: str | None = None) -> LLMClient:
    """Adaptador según `LLM_PROVIDER` (`anthropic` por defecto, `gemini` u `openai`).
    Con `task` (router|interpreter|simple|complex|judge), `LLM_MODEL_<TAREA>=proveedor:modelo` tiene prioridad; si no existe, conducta sin tarea."""
    if task is not None:
        if task not in LLM_TASKS:
            raise LLMError(f"tarea LLM desconocida: {task!r} ({'|'.join(LLM_TASKS)})")
        var = f"LLM_MODEL_{task.upper()}"
        spec = env_value(var).strip()
        if spec:
            provider, sep, model = spec.partition(":")
            provider, model = provider.strip().lower(), model.strip()
            if not sep or not model or provider not in LLM_PROVIDERS:
                raise LLMError(f"{var} inválida: se espera 'proveedor:modelo' con proveedor {'|'.join(LLM_PROVIDERS)} (p. ej. anthropic:claude-haiku-5-5)")
            return _build(provider, model)
    return _build(env_value("LLM_PROVIDER", "anthropic").lower())
