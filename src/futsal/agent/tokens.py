"""Contabilidad de tokens de la entrada de cada llamada al LLM (Fase 0 de la optimización de tokens).

Solo mide y describe; NO decide nada ni toca lo que se envía. Distingue tres naturalezas de dato, que nunca se mezclan:

- `provider`: lo que informa el proveedor (`usageMetadata` / `usage`): total de entrada, salida, razonamiento y caché. Es lo único facturable.
- `estimated`: tokens estimados LOCALMENTE a partir de caracteres (`CHARS_PER_TOKEN`). Sirven para repartir la entrada por origen;
  el proveedor NO da ese desglose. Nunca se presentan como consumo facturado.
- `chars`: caracteres exactos de cada parte (la base de la estimación).

Los precios no se inventan: `PRICES` está vacío y `estimate_cost` devuelve `None` hasta que se registre un precio verificado.
"""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from futsal.agent.llm import Message

CHARS_PER_TOKEN = 3.5  # mismo criterio que el evaluador offline (aproximación, no un tokenizador real)
SOURCE_PROVIDER = "provider"
SOURCE_ESTIMATED = "estimated"
SOURCE_CHARS = "chars"
BASIS = f"estimated_chars/{CHARS_PER_TOKEN}"

# Orígenes de la entrada, en el orden en que viajan al modelo.
ORIGINS = ("system_prompt", "history_rule", "context", "tools", "history", "question", "scope_notes", "prefetch",
           "assistant_turns", "tool_results", "other")


def est_tokens(chars: int) -> int:
    """Tokens estimados a partir de caracteres (redondeo hacia arriba; 0 caracteres = 0 tokens)."""
    return math.ceil(chars / CHARS_PER_TOKEN) if chars > 0 else 0


def _message_chars(m: Message) -> tuple[int, int]:
    """(caracteres de texto o llamadas del asistente, caracteres de resultados de herramientas) de un mensaje."""
    calls = sum(len(json.dumps(c.arguments, ensure_ascii=False, separators=(",", ":"))) + len(c.name) for c in m.tool_calls)
    results = sum(len(r.content) for r in m.tool_results)
    return len(m.text) + calls, results


def input_breakdown(*, system_prompt_chars: int, history_rule_chars: int, context_chars: int, tools_chars: int,
                    messages: Sequence[Message], history_messages: int, question_chars: int, note_chars: int,
                    provider_input_tokens: int | None = None, prefetch_chars: int = 0) -> dict[str, Any]:
    """Reparto de la entrada de UNA llamada por origen.

    `messages` es la lista exacta enviada; los primeros `history_messages` son el historial y el siguiente, la pregunta
    (con las notas de ámbito/foco que la aplicación le añade, contadas aparte). Lo que llega después es lo que ocurre dentro
    de la ejecución: respuestas del asistente, resultados de herramientas y mensajes de reconsulta (`other`).
    Si el proveedor informó el total, se calcula `calibration` = real / estimado (solo orientativo: el reparto sigue siendo estimado).
    """
    chars = dict.fromkeys(ORIGINS, 0)
    chars["system_prompt"], chars["history_rule"] = system_prompt_chars, history_rule_chars
    chars["context"], chars["tools"] = context_chars, tools_chars
    for index, m in enumerate(messages):
        text_chars, result_chars = _message_chars(m)
        if index < history_messages:
            chars["history"] += text_chars
        elif index == history_messages and m.role == "user":
            chars["question"] += question_chars
            chars["scope_notes"] += note_chars
            chars["prefetch"] += prefetch_chars  # resultado de la búsqueda previa inyectado en el mensaje (Fase 3A)
            chars["other"] += max(text_chars - question_chars - note_chars - prefetch_chars, 0)
        elif m.role == "assistant":
            chars["assistant_turns"] += text_chars
        elif m.role == "tool":
            chars["tool_results"] += result_chars
        else:
            chars["other"] += text_chars
    tokens = {k: est_tokens(v) for k, v in chars.items()}
    total = sum(tokens.values())
    return {
        "basis": BASIS,  # cómo se estimó: NO es un tokenizador del proveedor
        "chars": chars,
        "est_tokens": tokens,
        "est_total": total,
        "provider_input": provider_input_tokens,  # real (proveedor) o None si no lo informó
        "calibration": round(provider_input_tokens / total, 3) if provider_input_tokens and total else None,
    }


def tools_json_chars(tools: Sequence[dict[str, Any]]) -> int:
    """Caracteres de los esquemas de herramientas tal como el agente los entrega al adaptador (formato canónico, no el cable del proveedor)."""
    return len(json.dumps(list(tools), ensure_ascii=False, separators=(",", ":")))


# --- precios: estructura preparada, sin tarifas inventadas --------------------------------------------------------
@dataclass(frozen=True)
class Price:
    """Tarifa verificable de un modelo (USD por millón de tokens). `source` y `verified_on` son obligatorios: sin ellos no es un precio."""

    input_per_mtok: float
    output_per_mtok: float
    source: str
    verified_on: str
    cached_input_per_mtok: float | None = None


PRICES: dict[tuple[str, str], Price] = {}  # (proveedor, modelo) -> Price. Vacío a propósito: se rellena en una fase posterior


def estimate_cost(totals: dict[str, Any], price: Price | None) -> dict[str, Any] | None:
    """Coste de una ejecución a partir de los tokens REALES del proveedor y un precio verificado. Sin precio o sin tokens reales: `None`.

    Los tokens de razonamiento se facturan como salida (`billed_output_tokens`). La entrada en caché, si hay tarifa, se descuenta.
    """
    if price is None or totals.get("token_source") != SOURCE_PROVIDER:
        return None
    cached = totals.get("cached_input_tokens") or 0
    fresh = max((totals.get("input_tokens") or 0) - cached, 0)
    usd = fresh * price.input_per_mtok / 1e6 + (totals.get("billed_output_tokens") or 0) * price.output_per_mtok / 1e6
    usd += cached * (price.cached_input_per_mtok if price.cached_input_per_mtok is not None else price.input_per_mtok) / 1e6
    return {"usd": round(usd, 8), "price_source": price.source, "price_verified_on": price.verified_on}
