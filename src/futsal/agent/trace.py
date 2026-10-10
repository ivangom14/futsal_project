"""Traza estructurada de una ejecución del agente (JSONL). Sin secretos; los resultados completos son opcionales."""

import json
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
    latency_ms: float = 0.0
    answer: str = ""
    guardrail: str | None = None
    error: str | None = None

    def totals(self) -> dict[str, Any]:
        """Totales de la ejecución sumando cada llamada UNA vez (el historial reenviado ya va en su input)."""
        reported = [c for c in self.llm_calls if c.input_tokens is not None]
        inp = sum(c.input_tokens or 0 for c in reported)
        out = sum(c.output_tokens or 0 for c in reported)
        think = sum(c.thinking_tokens or 0 for c in reported)
        return {
            "llm_calls": len(self.llm_calls), "iterations": len(self.llm_calls),
            "tool_calls": len(self.tool_calls),
            "input_tokens": inp, "output_tokens": out, "thinking_tokens": think,
            "billed_output_tokens": out + think,
            "peak_input_tokens": max((c.input_tokens or 0 for c in reported), default=0),
            "calls_without_usage": len(self.llm_calls) - len(reported),
            "tokens_estimated": any(c.tokens_estimated for c in reported),
            "llm_ms": round(sum(c.latency_ms for c in self.llm_calls), 1),
            "tool_ms": round(sum(t.duration_ms for t in self.tool_calls), 1),
            "latency_ms": round(self.latency_ms, 1)}

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
