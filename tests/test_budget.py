"""Presupuesto GLOBAL REAL de las evaluaciones (auditoría): tope duro en el cliente LLM y acumulado entre tandas en `scripts/ab_arms.py`. Sin LLM ni red."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import ab_arms

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


# --- ab_arms: acumulado global entre brazos, tandas, reintentos y --fill-missing -------------------------------------------------------
def fake_runner(tmp: Path, per_batch_used: int, log: list[tuple[str, int]], statuses: dict[str, str] | None = None):
    def fake(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        if "futsal.agent.evaluate" in cmd:
            cap = int(cmd[cmd.index("--max-requests") + 1])
            out = Path(cmd[cmd.index("--output") + 1])
            only = cmd[cmd.index("--only") + 1].split(",") if "--only" in cmd else ["c1", "c2"]
            log.append((out.name, cap))
            used = min(per_batch_used, cap)
            cases = [{"case_id": cid, "status": (statuses or {}).get(cid, "pass"), "llm_calls": used if i == 0 else 0, "llm_retries": 0} for i, cid in enumerate(only)]
            out.write_text(json.dumps({"meta": {}, "cases": cases}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return fake


def test_global_budget_is_enforced_across_batches_and_stops_launching_more(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log: list[tuple[str, int]] = []
    monkeypatch.setattr(ab_arms.subprocess, "run", fake_runner(tmp_path, 40, log))
    code = ab_arms.main(["--arms", "legacy,prefetch", "--reps", "3", "--yes", "--max-requests", "50", "--max-total-requests", "120", "--out", str(tmp_path)])
    assert code == 3 and [c for _, c in log] == [50, 50, 40]  # 40 + 40 gastadas; la tercera solo recibe lo que queda (120 - 80)
    assert len(log) == 3  # las tres restantes NO se lanzan
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["budget"]["exhausted"] is True and manifest["max_total_requests"] == 120
    assert sum(r.get("requests_used", 0) for r in manifest["runs"]) == 120 and "Presupuesto global agotado" in capsys.readouterr().out


def test_a_batch_without_readable_results_is_charged_its_whole_grant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log: list[tuple[str, int]] = []

    def crash_silently(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        if "futsal.agent.evaluate" in cmd:
            log.append((Path(cmd[cmd.index("--output") + 1]).name, int(cmd[cmd.index("--max-requests") + 1])))
        return subprocess.CompletedProcess(cmd, 0, "", "")  # sin results.json

    monkeypatch.setattr(ab_arms.subprocess, "run", crash_silently)
    ab_arms.main(["--arms", "legacy,prefetch", "--reps", "1", "--yes", "--max-requests", "60", "--max-total-requests", "100", "--out", str(tmp_path)])
    assert [c for _, c in log] == [60, 40]  # la primera se cobra entera (no se sabe lo gastado): a la segunda solo le quedan 40


def test_without_an_explicit_total_the_default_cap_is_batches_times_per_batch_and_is_enforced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log: list[tuple[str, int]] = []
    monkeypatch.setattr(ab_arms.subprocess, "run", fake_runner(tmp_path, 40, log))
    assert ab_arms.main(["--arms", "legacy,prefetch", "--reps", "1", "--yes", "--max-requests", "50", "--out", str(tmp_path)]) == 0  # 2 tandas × 50 = 100 >= 80 usadas
    assert [c for _, c in log] == [50, 50]


def test_fill_missing_consumes_the_same_global_budget(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for arm in ("legacy", "prefetch"):
        (tmp_path / f"{arm}-r1.json").write_text(json.dumps({"meta": {}, "cases": [{"case_id": "a", "status": "pass"}, {"case_id": "b", "status": "not_run"}]}), encoding="utf-8")
    log: list[tuple[str, int]] = []
    monkeypatch.setattr(ab_arms.subprocess, "run", fake_runner(tmp_path, 30, log))
    code = ab_arms.main(["--arms", "legacy,prefetch", "--reps", "1", "--yes", "--fill-missing", "--max-requests", "50", "--max-total-requests", "70", "--out", str(tmp_path)])
    assert [c for _, c in log] == [50, 40]  # cada relleno recibe min(por tanda, lo que queda del MISMO global)
    out = capsys.readouterr().out
    assert code in (0, 3) and "Peticiones al LLM de esta ejecución" in out and "Casos sin ejecutar por tanda" in out


def test_fill_missing_stops_when_the_global_budget_is_spent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for arm in ("legacy", "prefetch"):
        (tmp_path / f"{arm}-r1.json").write_text(json.dumps({"meta": {}, "cases": [{"case_id": "b", "status": "not_run"}]}), encoding="utf-8")
    log: list[tuple[str, int]] = []
    monkeypatch.setattr(ab_arms.subprocess, "run", fake_runner(tmp_path, 50, log))
    code = ab_arms.main(["--arms", "legacy,prefetch", "--reps", "1", "--yes", "--fill-missing", "--max-requests", "50", "--max-total-requests", "50", "--out", str(tmp_path)])
    assert code == 3 and len(log) == 1  # la primera gasta los 50; la segunda ni se lanza


def test_requests_used_counts_calls_plus_retries_and_ignores_a_budget_refusal(tmp_path: Path) -> None:
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"cases": [{"llm_calls": 3, "llm_retries": 2}, {"llm_calls": 1, "llm_retries": 0, "detail": f"infraestructura (LLM): {BUDGET_EXHAUSTED}: 98 usadas"}]}), encoding="utf-8")
    assert ab_arms.requests_used(p) == 5  # el segundo caso fue rechazado sin enviar nada
    assert ab_arms.requests_used(tmp_path / "no.json") is None


def test_plan_shows_the_applied_global_cap_per_arm_per_batch_and_labels_estimates(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    called: list[Any] = []
    monkeypatch.setattr(ab_arms.subprocess, "run", lambda *a, **k: called.append(a))
    assert ab_arms.main(["--arms", "legacy,dynamic_tools", "--cases", "dynamic_rules", "--reps", "2", "--max-total-requests", "50", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert not called  # el plan no lanza nada
    assert "PRESUPUESTO GLOBAL MÁXIMO" in out and "SE APLICA" in out and ": 50" in out
    assert "por tanda ≈ 32" in out and "por brazo ≈ 64" in out and "es una estimación, no un tope" in out and "Tokens ESTIMADOS" in out
    assert "supera el presupuesto global" in out and "pendientes de ejecutar: 4" in out and "tope duro por tanda: 100" in out


def test_invalid_budget_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        ab_arms.main(["--arms", "legacy,prefetch", "--max-total-requests", "0", "--out", str(tmp_path)])


def test_summary_marks_budget_exhausted_runs_separately_from_429s(tmp_path: Path) -> None:
    from futsal.agent import arms, arms_summary
    sys.path.insert(0, str(Path(__file__).parent))
    import test_ab_summary as ab_tests
    for arm, status, detail in (("legacy", "pass", ""), ("prefetch", "not_run", f"infraestructura (LLM): {BUDGET_EXHAUSTED}: 98 usadas de 100")):
        d = ab_tests.doc("legacy", [ab_tests.case("c", status, detail=detail)])
        d["meta"]["agent_flags"] = arms.arm_env(arm)
        (tmp_path / f"{arm}-r1.json").write_text(json.dumps(d), encoding="utf-8")
    s = arms_summary.summarize(arms_summary.load_arms(tmp_path))
    assert s["cases"][0]["arms"]["prefetch"]["budget_exhausted"] == 1 and s["cases"][0]["arms"]["prefetch"]["rate_limited"] == 0
    assert "se agotó el presupuesto" in arms_summary.render(s)
