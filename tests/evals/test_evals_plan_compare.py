"""`plan` (sin ninguna llamada externa) y `compare` (regla de decisión §7)."""

import json
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest

from futsal.evals import cli
from futsal.evals import compare as cmp
from futsal.evals import report as reporting
from futsal.evals.plan import MEASURED, build_plan

ROOT = Path(__file__).parents[2]


# ----------------------------------------------------------------------------- plan
@pytest.fixture()
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("`plan` ha intentado una llamada externa")

    monkeypatch.setattr(httpx.Client, "send", boom)
    monkeypatch.setattr(httpx.AsyncClient, "send", boom)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket.socket, "connect_ex", boom)


def test_plan_makes_no_external_calls_and_uses_measured_averages(no_network: None, capsys: pytest.CaptureFixture[str]) -> None:
    plan = build_plan("smoke40", "dev", 3, ROOT)
    turns = plan["turns_per_repetition"]
    assert turns >= 40 and plan["questions"] == turns * 3 and plan["external_calls"] == 0
    assert plan["per_question"]["llm_calls"] == MEASURED["llm_calls"] == 2.65
    assert plan["per_question"]["input_tokens"] == MEASURED["input_tokens"] == 10800.0
    assert plan["estimated"]["llm_calls"] == round(turns * 3 * 2.65) and plan["estimated"]["input_tokens"] == round(turns * 3 * 10800)
    assert plan["cost_usd"] is None and "sin tarifa" in plan["cost_note"]  # sin tarifa verificada: null, nunca inventado
    # el plan completo de referencia: 190 turnos × 3 repeticiones = 570 preguntas ≈ 1.500 llamadas (docs/v2/evaluation.md §8)
    full = build_plan("futsito-v1", "all", 3, ROOT, max_requests=150)
    assert full["turns_per_repetition"] == 190 and full["questions"] == 570 and abs(full["estimated"]["llm_calls"] - 1510) <= 5
    assert full["budget_ok"] is False and full["suggested_max_requests"] > full["estimated"]["llm_calls"]
    assert build_plan("regression", "dev", 1, ROOT)["by_stratum"]["A-G"] == {"cases": 72, "turns": 65, "holdout": 0}
    assert cli.main(["plan", "--suite", "smoke40", "--reps", "1", "--root", str(ROOT), "--max-requests", "150"]) == 0
    assert "SIN llamadas externas" in capsys.readouterr().out


def test_plan_cost_with_verified_price_and_calibration(tmp_path: Path, no_network: None) -> None:
    plan = build_plan("smoke40", "dev", 1, ROOT, provider="gemini", model="gemini-3.5-flash-lite")  # config/llm_prices.json: 0,30 / 2,50
    est = plan["estimated"]
    assert plan["cost_usd"] == pytest.approx(est["input_tokens"] * 0.30 / 1e6 + est["output_tokens"] * 2.50 / 1e6, rel=1e-3)
    assert build_plan("smoke40", "dev", 1, ROOT, provider="gemini", model="modelo-sin-tarifa")["cost_usd"] is None
    # calibración con un results.json previo
    records = [_rec(f"c{i}", 0, "correct", llm=2, mcp=3, tin=1000, tout=50) for i in range(4)]
    results = _results(records)
    path = tmp_path / "prev.json"
    path.write_text(json.dumps(results), encoding="utf-8")
    cal = build_plan("smoke40", "dev", 1, ROOT, calibrate=path)
    assert cal["per_question"]["llm_calls"] == 2 and cal["per_question"]["input_tokens"] == 1000 and cal["per_question"]["output_tokens"] == 50


# ----------------------------------------------------------------------------- compare
def _rec(case: str, turn: int, verdict: str, rep: int = 0, stratum: str = "S1", llm: int = 2, mcp: int = 2, tin: int = 1000, tout: int = 50,
         cost: float | None = None, latency: float = 1000.0) -> dict[str, Any]:
    return {"case_id": case, "stratum": stratum, "holdout": False, "rep": rep, "turn": turn, "question": case, "verdict": verdict,
            "error_class": "critical" if verdict == "critical" else None, "reason": "", "answer": "", "tools_used": [], "run_id": "",
            "grade": {"tools_missing": [], "tools_unexpected": [], "facts_present": [case], "over_budget": False},
            "metrics": {"llm_calls": llm, "tool_calls": mcp, "context_calls": 0, "mcp_calls": mcp, "input_tokens": tin, "cached_tokens": 0,
                        "output_tokens": tout, "thinking_tokens": 0, "billed_output_tokens": tout, "token_source": "provider", "retries": 0,
                        "errors": 0, "exec_tier": "tier2", "cost_usd": cost},
            "timing": {"llm_call_ms": [latency / 2], "tool_call_ms": [latency / 4], "latency_ms": latency, "basis": "call"}}


def _results(records: list[dict[str, Any]], fp: str = "f" * 64, suite: str = "futsito-v1") -> dict[str, Any]:
    cases = {r["case_id"]: {"stratum": r["stratum"], "holdout": False, "capability": "v1", "status": "ok", "reason": "", "bindings": {},
                            "expected": [{"user": r["question"], "behavior": "answer", "tools_needed": []}]} for r in records}
    res: dict[str, Any] = {"schema": 1, "meta": {"suite": suite, "split": "dev", "snapshot": {"fingerprint": fp}}, "cases": cases, "records": records}
    res["summary"] = reporting.summarize(res)
    return res


def _run(verdicts: list[str], strata: list[str] | None = None, **kw: Any) -> dict[str, Any]:
    strata = strata or ["S1"] * len(verdicts)
    return _results([_rec(f"c{i:02d}", 0, v, stratum=s, **kw) for i, (v, s) in enumerate(zip(verdicts, strata, strict=True))])


def test_compare_better_when_ci_above_zero_and_no_new_criticals() -> None:
    a = _run(["correct"] * 20 + ["fail"] * 20)
    b = _run(["correct"] * 34 + ["fail"] * 6)
    out = cmp.compare(a, b)
    assert out["decision"] == cmp.BETTER and out["exit_code"] == 0 and out["accuracy_ci95"][0] > 0 and not out["new_critical"]
    assert cmp.compare(a, b) == out  # bootstrap con semilla fija: reproducible


def test_cost_or_latency_gain_with_new_criticals_is_always_rejected() -> None:
    a = _run(["correct"] * 30 + ["fail"] * 10, cost=0.01, latency=2000.0)
    b = _run(["correct"] * 30 + ["critical"] + ["fail"] * 9, cost=0.001, latency=200.0)
    out = cmp.compare(a, b)
    assert out["decision"] == cmp.REJECTED and out["new_critical"] and out["cheaper"] and out["faster"] and out["exit_code"] == 1
    assert "no lo compensa" in out["reasons"][0]


def test_equal_accuracy_but_significantly_cheaper_is_accepted() -> None:
    v = ["correct"] * 30 + ["fail"] * 10
    out = cmp.compare(_run(v, cost=0.01), _run(v, cost=0.004))
    assert out["decision"] == cmp.COST_BETTER and out["cheaper"] and out["exit_code"] == 0


def test_no_difference_demonstrated_and_stratum_drop_beyond_noise() -> None:
    v = ["correct"] * 20 + ["fail"] * 20
    assert cmp.compare(_run(v), _run(v))["decision"] == cmp.NO_DIFF
    strata = ["S1"] * 20 + ["S2"] * 20
    a = _run(["correct"] * 40, strata)
    b = _run(["correct"] * 20 + ["correct"] * 14 + ["fail"] * 6, strata)  # S2 baja 30 pts sin críticos nuevos
    out = cmp.compare(a, b)
    assert out["decision"] == cmp.REJECTED and any("S2" in r for r in out["reasons"])
    # el umbral de ruido lo fija la diferencia entre dos ejecuciones idénticas de la referencia
    noisy = _run(["correct"] * 20 + ["correct"] * 12 + ["fail"] * 8, strata)  # la referencia repetida ya varía 20 pts en S2
    out2 = cmp.compare(a, _run(["correct"] * 20 + ["correct"] * 15 + ["fail"] * 5, strata), noise_run=noisy)
    assert out2["strata"]["S2"]["noise_threshold"] == pytest.approx(0.4) and not any("estrato" in r for r in out2["reasons"])


def test_only_same_snapshot_and_suite_are_comparable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a, b = _run(["correct"] * 5), _results([_rec("c00", 0, "correct")], fp="0" * 64)
    with pytest.raises(cmp.IncomparableRuns, match="mismo snapshot"):
        cmp.compare(a, b)
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a), encoding="utf-8")
    pb.write_text(json.dumps(b), encoding="utf-8")
    assert cli.main(["compare", str(pa), str(pb)]) == 1
    assert "NO COMPARABLE" in capsys.readouterr().err
    pb.write_text(json.dumps(_run(["correct"] * 5)), encoding="utf-8")
    assert cli.main(["compare", str(pa), str(pb)]) == 2  # sin diferencia demostrada
