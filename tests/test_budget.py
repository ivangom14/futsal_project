"""Presupuesto GLOBAL REAL de las evaluaciones (auditoría): tope duro en el cliente LLM y acumulado entre tandas en `scripts/ab_arms.py`. Sin LLM ni red."""

from typing import Any

import pytest

from futsal.agent.llm import (
    BUDGET_EXHAUSTED,
    Attempt,
    BudgetedLLM,
    LLMError,
    LLMResponse,
)


class Inner:
    provider, model = "fake", "fake-model"

    def __init__(self, attempts_per_call: int = 1, fail: bool = False) -> None:
        self.n, self.attempts_per_call, self.fail, self.sent = 0, attempts_per_call, fail, 0

    def complete(self, system: str, messages: list[Any], tools: list[Any]) -> LLMResponse:
        self.n += 1
        attempts = [Attempt(i + 1, 200 if i == self.attempts_per_call - 1 else 429, None) for i in range(self.attempts_per_call)]
        self.sent += len(attempts)
        if self.fail:
            raise LLMError("fallo", attempts)
        return LLMResponse("ok", [], None, attempts)


# --- BudgetedLLM: tope duro, también con reintentos ---------------------------------------------------------------------------------
def test_budgeted_llm_never_sends_a_request_that_could_exceed_the_cap() -> None:
    inner = Inner()
    llm = BudgetedLLM(inner, max_requests=5, worst_case_per_call=3)
    for _ in range(3):
        llm.complete("s", [], [])  # usadas 0, 1, 2: en cada una, usadas + 3 (peor caso) <= 5
    with pytest.raises(LLMError, match=BUDGET_EXHAUSTED):
        llm.complete("s", [], [])  # usadas 3 + 3 = 6 > 5: la 4.ª llamada podría pasar del tope y no se envía
    assert inner.sent == 3 and llm.used == 3 and llm.used <= llm.max_requests


def test_retries_count_as_requests_and_the_cap_still_holds() -> None:
    inner = Inner(attempts_per_call=3)  # cada llamada lógica gasta 3 peticiones (2 reintentos)
    llm = BudgetedLLM(inner, max_requests=7, worst_case_per_call=3)
    llm.complete("s", [], [])
    llm.complete("s", [], [])
    assert llm.used == 6
    with pytest.raises(LLMError):
        llm.complete("s", [], [])
    assert inner.sent == 6 and inner.sent <= llm.max_requests and llm.refused == 1  # la rechazada no envió nada


def test_failed_calls_are_charged_and_a_refusal_sends_nothing() -> None:
    inner = Inner(attempts_per_call=2, fail=True)
    llm = BudgetedLLM(inner, max_requests=4, worst_case_per_call=2)
    for _ in range(2):
        with pytest.raises(LLMError):
            llm.complete("s", [], [])
    assert llm.used == 4
    sent = inner.sent
    with pytest.raises(LLMError, match=BUDGET_EXHAUSTED):
        llm.complete("s", [], [])
    assert inner.sent == sent


def test_budgeted_llm_is_transparent_for_provider_and_model() -> None:
    llm = BudgetedLLM(Inner(), 10, 1)
    assert llm.provider == "fake" and llm.model == "fake-model"
