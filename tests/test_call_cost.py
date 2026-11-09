"""Coste por llamada en la traza: solo con tokens del proveedor y tarifa registrada; nunca inventado."""
from types import SimpleNamespace

import pytest

from futsal.agent.agent import _call_cost
from futsal.agent.llm import Usage


def _llm(provider: str, model: str) -> SimpleNamespace:
    return SimpleNamespace(provider=provider, model=model)


def test_cost_with_provider_tokens_and_known_rate() -> None:
    u = Usage(10_000, 200, 0, cached_input_tokens=4_000)
    # gemini-3.5-flash-lite: 0,30 entrada, 0,03 cacheada, 2,50 salida (USD / 1M), verificado 2026-11-09
    expected = (6_000 * 0.30 + 4_000 * 0.03 + 200 * 2.50) / 1_000_000
    assert _call_cost(_llm("gemini", "gemini-3.5-flash-lite"), u) == pytest.approx(expected)  # type: ignore[arg-type]


def test_no_cost_without_rate_estimated_tokens_or_usage() -> None:
    assert _call_cost(_llm("gemini", "modelo-inexistente"), Usage(100, 10)) is None  # type: ignore[arg-type]
    assert _call_cost(_llm("gemini", "gemini-3.5-flash-lite"), Usage(100, 10, estimated=True)) is None  # type: ignore[arg-type]
    assert _call_cost(_llm("gemini", "gemini-3.5-flash-lite"), None) is None  # type: ignore[arg-type]
