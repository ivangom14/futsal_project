"""Cuota del proveedor agotada (espera de horas): el evaluador se detiene, `ab_arms` no sigue y el resumen la distingue de un 429 por minuto.
Y las medias de `arms_summary` solo cuentan repeticiones completas. Sin LLM, red ni base de datos."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))
import ab_arms

import test_ab_summary as ab_tests
from futsal.agent import arms, arms_summary, evaluate
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


# --- ab_arms: no lanza más tandas ni más relleno --------------------------------------------------------------------------------------
def quota_runner(log: list[str], quota_at: str, used: int = 7):
    def fake(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        if "futsal.agent.evaluate" in cmd:
            out = Path(cmd[cmd.index("--output") + 1])
            only = cmd[cmd.index("--only") + 1].split(",") if "--only" in cmd else ["a", "b"]
            log.append(out.name)
            hit = out.name.startswith(quota_at)
            cases = [{"case_id": cid, "status": ("not_run" if hit and i else "pass"), "llm_calls": used if i == 0 else 1 if hit else 0, "llm_retries": 0,
                      "detail": DETAIL if hit and i else ""} for i, cid in enumerate(only)]
            out.write_text(json.dumps({"meta": {}, "cases": cases}), encoding="utf-8")
            return subprocess.CompletedProcess(cmd, QUOTA_EXIT_CODE if hit else 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return fake


def test_ab_arms_stops_on_quota_exhaustion_keeps_results_and_says_when_it_comes_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log: list[str] = []
    monkeypatch.setattr(ab_arms.subprocess, "run", quota_runner(log, "prefetch-r1"))
    code = ab_arms.main(["--arms", "legacy,prefetch", "--reps", "3", "--yes", "--max-requests", "50", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == QUOTA_EXIT_CODE and log == ["legacy-r1.json", "prefetch-r1.json"]  # ninguna tanda más
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["quota_exhausted"]["at"] == "prefetch r1" and "70277" in manifest["quota_exhausted"]["hint"] and "UTC" in manifest["quota_exhausted"]["hint"]
    assert [r["requests_used"] for r in manifest["runs"]] == [7, 18]  # 7 de legacy; prefetch: 7 + 11 casos con la llamada rechazada; se cobra lo realmente usado, no la concesión de 50
    assert "CUOTA DEL PROVEEDOR AGOTADA" in out and "--fill-missing" in out and (tmp_path / "prefetch-r1.json").exists()


def test_fill_missing_stops_on_quota_but_keeps_what_it_completed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for arm in ("legacy", "prefetch"):
        (tmp_path / f"{arm}-r1.json").write_text(json.dumps({"meta": {}, "cases": [{"case_id": "a", "status": "not_run"}, {"case_id": "b", "status": "not_run"}]}), encoding="utf-8")
    log: list[str] = []
    monkeypatch.setattr(ab_arms.subprocess, "run", quota_runner(log, "legacy-r1"))
    code = ab_arms.main(["--arms", "legacy,prefetch", "--reps", "1", "--yes", "--fill-missing", "--max-requests", "50", "--out", str(tmp_path)])
    assert code == QUOTA_EXIT_CODE and len(log) == 1  # el segundo brazo ni se lanza
    merged = json.loads((tmp_path / "legacy-r1.json").read_text(encoding="utf-8"))
    assert [(c["case_id"], c["status"]) for c in merged["cases"]] == [("a", "pass"), ("b", "not_run")]  # lo completado se conserva; lo demás sigue pendiente
    assert (tmp_path / "legacy-r1.orig.json").exists()


def test_the_quota_exit_code_is_distinct_from_the_other_codes() -> None:
    assert QUOTA_EXIT_CODE not in (0, 1, 2, 3)


# --- resumen: cuota frente a 429 y medias sin repeticiones vacías ---------------------------------------------------------------------
def write_runs(folder: Path, plan: dict[str, list[tuple[str, str]]]) -> None:
    """plan: brazo -> por repetición, lista de (estado, detalle) de los casos."""
    for arm, reps in plan.items():
        for n, cases in enumerate(reps, 1):
            d = ab_tests.doc("legacy", [ab_tests.case(f"c{i}", st, detail=det, llm=3 if st in ("pass", "fail") else 0, inp=9000 if st in ("pass", "fail") else 0)
                                        for i, (st, det) in enumerate(cases)])
            d["meta"]["agent_flags"] = arms.arm_env(arm)
            (folder / f"{arm}-r{n}.json").write_text(json.dumps(d), encoding="utf-8")


OLD_QUOTA = "infraestructura (LLM): LLM HTTP 429: el servidor pide esperar 70277s, por encima del máximo permitido (20s); no se reintenta"
RPM = "infraestructura (LLM): LLM HTTP 429: el servidor pide esperar 47s, por encima del máximo permitido (20s); no se reintenta"


def test_quota_is_told_apart_from_a_per_minute_429_even_in_old_results(tmp_path: Path) -> None:
    write_runs(tmp_path, {"legacy": [[("pass", ""), ("pass", "")]], "prefetch": [[("not_run", DETAIL), ("not_run", OLD_QUOTA), ("not_run", RPM)]]})
    s = arms_summary.summarize(arms_summary.load_arms(tmp_path))
    case = lambda i: s["cases"][i]["arms"]["prefetch"]
    assert (case(0)["quota_exhausted"], case(0)["rate_limited"]) == (1, 0)  # marcador nuevo
    assert (case(1)["quota_exhausted"], case(1)["rate_limited"]) == (1, 0)  # texto antiguo con una espera de horas
    assert (case(2)["quota_exhausted"], case(2)["rate_limited"]) == (0, 1)  # 47 s: límite por minuto
    text = arms_summary.render(s)
    assert "CUOTA" in text and "[cuota×1]" in text and "[429×1]" in text and "subir --min-interval no ayuda" in text


def test_means_only_use_complete_runs_and_empty_ones_are_flagged(tmp_path: Path) -> None:
    full = [("pass", ""), ("pass", ""), ("pass", "")]
    write_runs(tmp_path, {"legacy": [full, full, [("pass", ""), ("not_run", DETAIL), ("not_run", DETAIL)], [("not_run", DETAIL)] * 3],
                          "prefetch": [full, full, [("not_run", DETAIL)] * 3, [("not_run", DETAIL)] * 3]})
    s = arms_summary.summarize(arms_summary.load_arms(tmp_path))
    t = s["totals"]["legacy"]
    assert (t["runs_total"], t["runs_complete"], t["incomplete_runs"]) == (4, 2, ["3", "4"])
    assert t["mean_llm_calls"] == 9 and t["mean_pass"] == 3 and t["mean_input_per_llm_call"] == 3000  # sin que las vacías las arrastren a cero
    text = arms_summary.render(s)
    assert "medias sobre 2 de 4 repeticiones completas" in text and "INCOMPLETA" in text and "None" not in text and "| — |" in text


def test_a_brazo_without_any_complete_run_shows_no_means(tmp_path: Path) -> None:
    write_runs(tmp_path, {"legacy": [[("pass", "")] * 2], "prefetch": [[("pass", ""), ("not_run", DETAIL)]]})
    s = arms_summary.summarize(arms_summary.load_arms(tmp_path))
    assert s["totals"]["prefetch"]["mean_pass"] is None and s["totals"]["prefetch"]["runs_complete"] == 0
    assert "SIN repeticiones completas" in arms_summary.render(s)


def test_per_case_means_ignore_not_run_repetitions(tmp_path: Path) -> None:
    write_runs(tmp_path, {"legacy": [[("pass", "")], [("not_run", DETAIL)], [("pass", "")]], "prefetch": [[("pass", "")], [("pass", "")], [("pass", "")]]})
    s = arms_summary.summarize(arms_summary.load_arms(tmp_path))
    assert s["cases"][0]["arms"]["legacy"]["mean_input_tokens"] == 9000 and s["cases"][0]["arms"]["legacy"]["mean_llm_calls"] == 3
