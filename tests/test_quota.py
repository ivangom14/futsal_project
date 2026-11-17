"""Cuota del proveedor agotada (espera de horas): el evaluador se detiene, `ab_arms` no sigue y el resumen la distingue de un 429 por minuto.
Y las medias de `arms_summary` solo cuentan repeticiones completas. Sin LLM, red ni base de datos."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))


from futsal.agent import evaluate
from futsal.agent.arms import QUOTA_EXIT_CODE
from futsal.agent.llm import QUOTA_MARKER, LLMError, LLMQuotaError
from test_llm_retries import OK, _complete, _llm

DETAIL = f"infraestructura (LLM): {QUOTA_MARKER}: LLM HTTP 429: el servidor pide esperar 70277s (≈19.5 h), por encima del máximo permitido (20s); no se reintenta"


# --- cliente: una espera de horas es cuota, una de segundos no ----------------------------------------------------------------------
@pytest.mark.parametrize("retries", [0, 2])
def test_a_wait_of_hours_is_a_quota_error_whatever_the_retry_policy(retries: int) -> None:
    sleeps: list[float] = []
    llm, calls = _llm([httpx.Response(429, headers={"Retry-After": "70277"}), OK], sleeps, retries=retries, max_wait=20)
    with pytest.raises(LLMQuotaError, match=QUOTA_MARKER) as exc:
        _complete(llm)
    assert sleeps == [] and len(calls) == 1 and exc.value.retry_after == 70277 and "no se reintenta" in str(exc.value)


def test_a_wait_of_seconds_is_not_a_quota_error() -> None:
    sleeps: list[float] = []
    llm, _ = _llm([httpx.Response(429, headers={"Retry-After": "120"}), OK], sleeps, max_wait=20)
    with pytest.raises(LLMError, match="no se reintenta") as exc:
        _complete(llm)
    assert not isinstance(exc.value, LLMQuotaError) and QUOTA_MARKER not in str(exc.value)


# --- evaluador: se detiene en el primer caso afectado ------------------------------------------------------------------------------
def fake_case(i: int) -> SimpleNamespace:
    return SimpleNamespace(id=f"c{i}", category="x", question=f"q{i}", facts=None)


def test_the_evaluator_stops_at_the_first_quota_case_and_runs_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = [fake_case(i) for i in range(5)]
    ran: list[str] = []

    async def fake_run_case(c: Any, *a: Any, **k: Any) -> evaluate.CaseResult:
        ran.append(c.id)
        if c.id == "c2":
            return evaluate.CaseResult(c.id, c.category, c.question, "not_run", detail=DETAIL, llm_calls=1)
        return evaluate.CaseResult(c.id, c.category, c.question, "pass", llm_calls=2)

    monkeypatch.setattr(evaluate, "build_cases", lambda truth: cases)
    monkeypatch.setattr(evaluate, "select_cases", lambda cs, **kw: cs)
    monkeypatch.setattr(evaluate, "run_case", fake_run_case)
    messages: list[str] = []
    results = asyncio.run(evaluate.evaluate(None, None, "live", None, on_progress=messages.append))  # type: ignore[arg-type]
    assert ran == ["c0", "c1", "c2"]  # c3 y c4 no se ejecutan: ni una petición más
    assert [r.status for r in results] == ["pass", "pass", "not_run", "not_run", "not_run"]
    assert all(QUOTA_MARKER in r.detail for r in results[2:]) and "se detuvo en c2" in results[4].detail
    assert any("CUOTA DEL PROVEEDOR AGOTADA" in m for m in messages)


def test_an_ordinary_not_run_does_not_stop_the_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = [fake_case(i) for i in range(3)]
    ran: list[str] = []

    async def fake_run_case(c: Any, *a: Any, **k: Any) -> evaluate.CaseResult:
        ran.append(c.id)
        return evaluate.CaseResult(c.id, c.category, c.question, "not_run", detail="infraestructura (LLM): LLM HTTP 429: el servidor pide esperar 47s, por encima del máximo permitido (20s)")

    monkeypatch.setattr(evaluate, "build_cases", lambda truth: cases)
    monkeypatch.setattr(evaluate, "select_cases", lambda cs, **kw: cs)
    monkeypatch.setattr(evaluate, "run_case", fake_run_case)
    asyncio.run(evaluate.evaluate(None, None, "live", None))  # type: ignore[arg-type]
    assert ran == ["c0", "c1", "c2"]  # un 429 por minuto sigue como antes


def test_the_quota_exit_code_is_distinct_from_the_other_codes() -> None:
    assert QUOTA_EXIT_CODE not in (0, 1, 2, 3)
