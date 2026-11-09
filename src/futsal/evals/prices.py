"""Tarifas de LLM: UNA sola fuente de verdad, `agent.llm.estimate_cost` (lee `config/llm_prices.json`). Sin tarifa el coste es `null`."""

import json
from typing import Any

from futsal.agent.llm import PRICES_FILE, Usage, _load_prices, estimate_cost

Model = tuple[str, str]  # (proveedor, modelo)


def has_price(provider: str | None, model: str | None) -> bool:
    return isinstance(_load_prices().get(f"{provider}:{model}"), dict)


def cost_usd(provider: str | None, model: str | None, totals: dict[str, Any]) -> float | None:
    """Coste de una pregunta con los tokens REALES del proveedor (`provider`); `measured_zero` = 0 con tarifa; estimados = null."""
    source = totals.get("token_source")
    if not has_price(provider, model):
        return None
    if source == "measured_zero":
        return 0.0
    if source != "provider":
        return None
    usage = Usage(int(totals.get("input_tokens") or 0), int(totals.get("output_tokens") or 0), int(totals.get("thinking_tokens") or 0), None,
                  False, int(totals.get("cached_input_tokens") or 0))
    cost = estimate_cost(str(provider), str(model), usage)
    return round(cost, 8) if cost is not None else None


def price_info(provider: str | None, model: str | None) -> dict[str, Any]:
    """Tarifa usada para el informe: valores, `source` y `verified_at` (del modelo o, si falta, del fichero)."""
    rate = _load_prices().get(f"{provider}:{model}")
    if not isinstance(rate, dict):
        return {"model": f"{provider}:{model}", "note": "sin tarifa en config/llm_prices.json: coste = null"}
    try:
        file_date = json.loads(PRICES_FILE.read_text(encoding="utf-8")).get("verified_at")
    except (OSError, ValueError):
        file_date = None
    return {"model": f"{provider}:{model}", "input": rate.get("input"), "cached_input": rate.get("cached_input"), "output": rate.get("output"),
            "source": rate.get("source"), "verified_at": rate.get("verified_at") or file_date, "unit": "USD por 1M de tokens"}
