"""Registro de modelos del benchmark (`evals/bench/models.yaml`): carga, validación, construcción del cliente y coste por llamada.

Añadir un modelo de un proveedor con adaptador = añadir una entrada al YAML (sin tocar código). Un proveedor nuevo necesita su
adaptador en `agent/llm.py` y una línea en `ADAPTERS`.

Coste: siempre CALCULADO con tarifas y tokens; nunca es un importe facturado (la API no lo devuelve). Cada coste lleva su base:
`provider` (tokens medidos por el proveedor), `estimated` (tokens estimados por el agente) o `unavailable` (falta una tarifa o los
tokens): en ese caso es `None`, no cero.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from futsal.agent.llm import AnthropicLLM, GeminiLLM, LLMClient, LLMError, OpenAILLM, RetryPolicy
from futsal.config.settings import env_value

REGISTRY_FILE = Path("evals/bench/models.yaml")
ID_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")


class RegistryError(ValueError):
    """Configuración de modelos inválida o incompleta."""


@dataclass(frozen=True)
class Adapter:
    build: Callable[..., LLMClient]
    params: tuple[str, ...]  # parámetros de `params` que admite
    default_base_url: str
    input_includes_cached: bool  # Gemini y OpenAI cuentan la entrada cacheada dentro de `input_tokens`; Anthropic aparte


def _anthropic(key: str, model: str, base_url: str, max_tokens: int, params: dict[str, Any]) -> LLMClient:
    return AnthropicLLM(key, model, base_url, max_tokens=max_tokens, retry=RetryPolicy.from_env(), prompt_cache=False)


def _gemini(key: str, model: str, base_url: str, max_tokens: int, params: dict[str, Any]) -> LLMClient:
    return GeminiLLM(key, model, base_url, max_tokens=max_tokens, retry=RetryPolicy.from_env(),
                     thinking_level=params.get("thinking_level"))


def _openai(key: str, model: str, base_url: str, max_tokens: int, params: dict[str, Any]) -> LLMClient:
    return OpenAILLM(key, model, base_url, max_tokens=max_tokens, retry=RetryPolicy.from_env(),
                     reasoning_effort=params.get("reasoning_effort"))


ADAPTERS: dict[str, Adapter] = {
    "anthropic": Adapter(_anthropic, (), "https://api.anthropic.com", False),
    "gemini": Adapter(_gemini, ("thinking_level",), "https://generativelanguage.googleapis.com", True),
    "openai": Adapter(_openai, ("reasoning_effort",), "https://api.openai.com", True),
}


@dataclass(frozen=True)
class LongContext:
    above_input_tokens: int
    input: float | None
    output: float | None
    cached_input: float | None = None


@dataclass(frozen=True)
class Pricing:
    input: float | None
    output: float | None
    cached_input: float | None = None
    cache_write: float | None = None
    long_context: LongContext | None = None
    other: tuple[dict[str, Any], ...] = ()
    source: str | None = None
    checked_at: str | None = None

    @property
    def status(self) -> str:
        if self.input is None or self.output is None:
            return "unknown"
        return "partial" if self.cached_input is None else "known"

    def as_dict(self) -> dict[str, Any]:
        lc = self.long_context
        return {"input": self.input, "output": self.output, "cached_input": self.cached_input, "cache_write": self.cache_write,
                "long_context": None if lc is None else {"above_input_tokens": lc.above_input_tokens, "input": lc.input,
                                                         "output": lc.output, "cached_input": lc.cached_input},
                "other": list(self.other), "source": self.source, "checked_at": self.checked_at, "status": self.status,
                "unit": "USD por 1M de tokens"}


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    provider: str
    adapter: str
    model: str
    version: str
    credentials_env: str
    pricing: Pricing
    enabled: bool = True
    free_tier: bool = False
    max_output_tokens: int = 1024
    params: dict[str, Any] = field(default_factory=dict)
    base_url: str | None = None
    min_interval_s: float = 0.0
    max_requests: int = 600

    def has_credentials(self) -> bool:
        return bool(env_value(self.credentials_env).strip())

    def as_dict(self) -> dict[str, Any]:
        """Descripción para los informes. Nunca incluye el valor de la credencial (solo el nombre de la variable)."""
        return {"id": self.id, "label": self.label, "provider": self.provider, "adapter": self.adapter, "model": self.model,
                "version": self.version, "credentials_env": self.credentials_env, "enabled": self.enabled, "free_tier": self.free_tier,
                "max_output_tokens": self.max_output_tokens, "params": dict(self.params), "base_url": self.base_url,
                "limits": {"min_interval_s": self.min_interval_s, "max_requests": self.max_requests}, "pricing": self.pricing.as_dict()}


def _num(raw: dict[str, Any], key: str, where: str) -> float | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise RegistryError(f"{where}: {key} debe ser un número ≥ 0 o null (desconocido), no {value!r}")
    return float(value)


def _pricing(raw: Any, where: str) -> Pricing:
    if not isinstance(raw, dict):
        raise RegistryError(f"{where}: falta `pricing` (usa null en lo que no se sepa)")
    lc_raw = raw.get("long_context")
    lc = None
    if lc_raw is not None:
        if not isinstance(lc_raw, dict) or not isinstance(lc_raw.get("above_input_tokens"), int):
            raise RegistryError(f"{where}: long_context necesita above_input_tokens (entero)")
        lc = LongContext(int(lc_raw["above_input_tokens"]), _num(lc_raw, "input", where), _num(lc_raw, "output", where),
                         _num(lc_raw, "cached_input", where))
    checked = raw.get("checked_at")
    return Pricing(_num(raw, "input", where), _num(raw, "output", where), _num(raw, "cached_input", where),
                   _num(raw, "cache_write", where), lc, tuple(raw.get("other") or ()), raw.get("source"),
                   None if checked is None else str(checked))


def parse_model(raw: dict[str, Any]) -> ModelSpec:
    mid = str(raw.get("id", ""))
    where = f"modelo {mid or '?'}"
    if not mid or not set(mid) <= ID_CHARS:
        raise RegistryError(f"{where}: `id` obligatorio, en minúsculas, dígitos y - _ .")
    for key in ("provider", "adapter", "model", "credentials_env"):
        if not raw.get(key):
            raise RegistryError(f"{where}: falta `{key}`")
    adapter = str(raw["adapter"])
    if adapter not in ADAPTERS:
        raise RegistryError(f"{where}: adaptador {adapter!r} no disponible ({', '.join(sorted(ADAPTERS))}); un proveedor nuevo "
                            "necesita su adaptador en agent/llm.py y su alta en bench/registry.py")
    params = dict(raw.get("params") or {})
    unknown = sorted(set(params) - set(ADAPTERS[adapter].params))
    if unknown:
        raise RegistryError(f"{where}: parámetros no admitidos por el adaptador {adapter}: {unknown} "
                            f"(admite {list(ADAPTERS[adapter].params) or 'ninguno'})")
    limits = raw.get("limits") or {}
    max_out = raw.get("max_output_tokens", 1024)
    if not isinstance(max_out, int) or max_out <= 0:
        raise RegistryError(f"{where}: max_output_tokens debe ser un entero > 0")
    return ModelSpec(
        id=mid, label=str(raw.get("label") or mid), provider=str(raw["provider"]), adapter=adapter, model=str(raw["model"]),
        version=str(raw.get("version") or "no indicada"), credentials_env=str(raw["credentials_env"]),
        pricing=_pricing(raw.get("pricing"), where), enabled=bool(raw.get("enabled", True)), free_tier=bool(raw.get("free_tier", False)),
        max_output_tokens=max_out, params={k: v for k, v in params.items() if v is not None}, base_url=raw.get("base_url"),
        min_interval_s=float(limits.get("min_interval_s", 0.0)), max_requests=int(limits.get("max_requests", 600)))


def load_registry(path: Path | None = None, root: Path = Path(".")) -> dict[str, ModelSpec]:
    target = path or root / REGISTRY_FILE
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    models = raw.get("models")
    if not isinstance(models, list) or not models:
        raise RegistryError(f"{target}: se esperaba una lista `models`")
    specs: dict[str, ModelSpec] = {}
    for m in models:
        if not isinstance(m, dict):
            raise RegistryError(f"{target}: cada modelo debe ser un objeto")
        spec = parse_model(m)
        if spec.id in specs:
            raise RegistryError(f"{target}: id repetido {spec.id!r}")
        specs[spec.id] = spec
    return specs


def select(specs: dict[str, ModelSpec], ids: list[str] | None) -> list[ModelSpec]:
    """Modelos pedidos (en ese orden) o, sin lista, los activados. Pedir uno desactivado es válido (se activa para esa ejecución)."""
    if not ids:
        chosen = [s for s in specs.values() if s.enabled]
        if not chosen:
            raise RegistryError("no hay modelos activados (enabled: true) y no se ha indicado ninguno con --models")
        return chosen
    missing = [i for i in ids if i not in specs]
    if missing:
        raise RegistryError(f"modelos no configurados: {missing} (disponibles: {', '.join(specs)})")
    return [specs[i] for i in dict.fromkeys(ids)]


def build_llm(spec: ModelSpec) -> LLMClient:
    """Cliente real del modelo. Lee la clave de `credentials_env` en el momento (nunca se guarda ni se registra)."""
    key = env_value(spec.credentials_env).strip()  # entorno o .env, como el resto del proyecto
    if not key:
        raise LLMError(f"{spec.credentials_env} no definida: no se puede ejecutar {spec.id}")
    adapter = ADAPTERS[spec.adapter]
    return adapter.build(key, spec.model, (spec.base_url or adapter.default_base_url).rstrip("/"), spec.max_output_tokens, spec.params)


# ----------------------------------------------------------------------------- coste por llamada
@dataclass(frozen=True)
class CallCost:
    usd: float | None
    basis: str  # provider | estimated | unavailable
    reason: str = ""
    usd_no_cache: float | None = None  # misma llamada si toda la entrada se cobrara a tarifa normal (comparación sin ventaja de caché)


def call_cost(spec: ModelSpec, call: dict[str, Any]) -> CallCost:
    """Coste de UNA llamada al LLM a partir de sus tokens (`input`, `cached`, `output`, `thinking`, `token_source`)."""
    source = str(call.get("token_source") or "unreported")
    if source not in ("provider", "estimated"):
        return CallCost(None, "unavailable", "el proveedor no informó tokens")
    p = spec.pricing
    tokens_in, cached = int(call.get("input") or 0), int(call.get("cached") or 0)
    billed_out = int(call.get("output") or 0) + int(call.get("thinking") or 0)
    includes = ADAPTERS[spec.adapter].input_includes_cached
    uncached = max(tokens_in - cached, 0) if includes else tokens_in
    total_in = uncached + cached
    rate_in, rate_out, rate_cached = p.input, p.output, p.cached_input
    if p.long_context is not None and total_in > p.long_context.above_input_tokens:
        rate_in, rate_out, rate_cached = p.long_context.input, p.long_context.output, p.long_context.cached_input
    if rate_in is None or rate_out is None:
        return CallCost(None, "unavailable", "tarifa de entrada o salida desconocida")
    no_cache = (total_in * rate_in + billed_out * rate_out) / 1_000_000
    if cached and rate_cached is None:
        return CallCost(None, "unavailable", "hay entrada cacheada y su tarifa es desconocida", round(no_cache, 10))
    usd = (uncached * rate_in + cached * (rate_cached or 0.0) + billed_out * rate_out) / 1_000_000
    return CallCost(round(usd, 10), source, "", round(no_cache, 10))
