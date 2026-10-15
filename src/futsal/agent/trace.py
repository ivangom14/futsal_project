"""Traza estructurada de una ejecución del agente (JSONL). Sin secretos; los resultados completos son opcionales."""

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from futsal.config.settings import env_value

_SECRET_VARS = ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL")


def redact(text: str) -> str:
    """Enmascara cualquier valor secreto del entorno que aparezca por error en un texto."""
    for name in _SECRET_VARS:
        value = env_value(name)
        if value and len(value) >= 8:
            text = text.replace(value, "***")
            if name == "DATABASE_URL" and "@" in value and ":" in value.split("@")[0]:
                password = value.split("@")[0].rsplit(":", 1)[-1]
                if len(password) >= 4:
                    text = text.replace(password, "***")
    return text


@dataclass
class LlmCallRecord:
    index: int
    latency_ms: float
    input_tokens: int | None  # de ESTA llamada (incluye todo el historial reenviado)
    output_tokens: int | None
    thinking_tokens: int | None
    total_tokens: int | None
    tokens_estimated: bool
    tool_calls_requested: int
    error: str | None = None
    retries: int = 0  # reintentos HTTP de esta llamada lógica (0 = salió a la primera)
    attempts: list[dict[str, Any]] = field(default_factory=list)  # un dict por intento: number/status/error/waited_s
    retry_wait_ms: float = 0.0  # parte de latency_ms que fue espera de reintentos
    token_source: str = "unreported"  # provider | estimated | unreported
    cached_input_tokens: int | None = None  # entrada servida desde la caché del proveedor (si la informa)
    paced_ms: float = 0.0  # pausa de ritmo (--min-interval) ANTES de la llamada; no forma parte de latency_ms


def _overall_source(calls: list["LlmCallRecord"]) -> str:
    sources = {c.token_source for c in calls}
    if not sources:
        return "unreported"
    return sources.pop() if len(sources) == 1 else "mixed"


@dataclass
class ToolCallRecord:
    index: int
    llm_call: int  # índice de la llamada al LLM que la pidió
    name: str
    arguments: dict[str, Any]
    duration_ms: float
    is_error: bool
    result_chars_raw: int  # lo que devolvió el MCP
    result_chars_sent: int  # lo que se envió al LLM tras compactar
    result_summary: str
    truncated: bool
    result: str | None = None  # texto enviado al LLM; solo se serializa con include_results


@dataclass
class RunRecord:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    question: str = ""
    provider: str | None = None
    model: str | None = None
    max_tool_calls: int = 0
    llm_calls: list[LlmCallRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    context_ms: float = 0.0  # contexto previo (consultas MCP sin LLM); solo en la ejecución que lo construye
    context_tool_calls: int = 0
    latency_ms: float = 0.0  # reloj de pared de la ejecución (incluye las pausas de ritmo)
    prefix_chars: dict[str, int] = field(default_factory=dict)  # caracteres del prefijo fijo: system_prompt/context/tools
    answer: str = ""
    guardrail: str | None = None
    error: str | None = None

    def totals(self) -> dict[str, Any]:
        """Totales de la ejecución sumando cada llamada UNA vez (el historial reenviado ya va en su input)."""
        reported = [c for c in self.llm_calls if c.input_tokens is not None]
        paced = sum(c.paced_ms for c in self.llm_calls)
        inp = sum(c.input_tokens or 0 for c in reported)
        out = sum(c.output_tokens or 0 for c in reported)
        think = sum(c.thinking_tokens or 0 for c in reported)
        return {
            "llm_calls": len(self.llm_calls), "iterations": len(self.llm_calls),
            "tool_calls": len(self.tool_calls),
            "input_tokens": inp, "output_tokens": out, "thinking_tokens": think,
            "billed_output_tokens": out + think,
            "cached_input_tokens": sum(c.cached_input_tokens or 0 for c in reported),
            "peak_input_tokens": max((c.input_tokens or 0 for c in reported), default=0),
            "calls_without_usage": len(self.llm_calls) - len(reported),
            "tokens_estimated": any(c.tokens_estimated for c in reported),
            "token_source": _overall_source(self.llm_calls),
            "llm_retries": sum(c.retries for c in self.llm_calls),
            "retry_wait_ms": round(sum(c.retry_wait_ms for c in self.llm_calls), 1),
            "llm_ms": round(sum(c.latency_ms for c in self.llm_calls), 1),
            "tool_ms": round(sum(t.duration_ms for t in self.tool_calls), 1),
            "paced_ms": round(paced, 1),
            "latency_ms": round(max(self.latency_ms - paced, 0.0), 1),  # sin las pausas de ritmo
            "wall_ms": round(self.latency_ms, 1),
            "prefix_chars": dict(self.prefix_chars)}

    def to_dict(self, include_results: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_results:
            for t in data["tool_calls"]:
                t.pop("result", None)
        data["error"] = redact(self.error) if self.error else None
        data["answer"] = redact(self.answer)
        data["totals"] = self.totals()
        return data


def write_jsonl(path: Path, record: RunRecord, include_results: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record.to_dict(include_results), ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# --- saneado para compartir trazas (sin resultados de tools, sin claves ni cadenas de conexión) ------------------
_SECRET_PATTERNS = (
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "***"),  # claves de Google/Gemini
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "***"),  # claves tipo Anthropic/OpenAI
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{10,}"), "Bearer ***"),
    (re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^\s\"'<>]+"), "postgresql://***"),
    (re.compile(r"(?i)(api[_-]?key|token|password|passwd|secret)(\s*[=:]\s*)[^\s,;\"']{4,}"), r"\1\2***"))
MAX_SANITIZED_CHARS = 1500


def sanitize_text(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    text = redact(text)
    return text if len(text) <= MAX_SANITIZED_CHARS else text[:MAX_SANITIZED_CHARS] + "…[recortado]"


def _sanitize(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items() if k != "result"}  # los datos devueltos por las tools no se comparten
    if isinstance(o, list):
        return [_sanitize(v) for v in o]
    return sanitize_text(o) if isinstance(o, str) else o


def sanitize_trace_file(src: Path, dst: Path) -> int:
    """Copia una traza JSONL a `dst` sin resultados de tools y con claves/conexiones enmascaradas. Devuelve nº de líneas."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with src.open(encoding="utf-8") as fin, dst.open("w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            fout.write(json.dumps(_sanitize(json.loads(line)), ensure_ascii=False, separators=(",", ":")) + "\n")
            n += 1
    return n
