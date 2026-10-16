"""Fase 13: identidad de ejecución, comparación, regresiones, umbrales, ejecuciones incompletas y CLI. Offline, sin red ni LLM."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from futsal.agent import evaluate as ev
from futsal.agent.evaluate import CaseResult, summarize_results
from futsal.agent.regression import (
    Thresholds,
    compare,
    list_runs,
    overall_status,
    render_runs,
    render_verdict,
)
from futsal.agent.report import build_meta, render_markdown, results_payload, suite_version, write_report
from futsal.agent.trace import LlmCallRecord, RunRecord

META = {"generated_at": "2026-10-17T10:00:00+00:00", "mode": "live", "suite": "all", "provider": "gemini", "model": "m",
        "commit": "abc1234", "commit_dirty": False, "run_id": "r-1", "suite_version": "all-3-aaaa", "args": {},
        "preflight": {"same_database": "same", "detail": "ok"}}


def _r(cid: str, status: str = "pass", **kw: Any) -> CaseResult:
    base: dict[str, Any] = dict(case_id=cid, category="x", question="¿q?", status=status, criteria="c", llm_calls=2,
                                input_tokens=5000, output_tokens=100, token_source="provider", latency_ms=2000.0, tool_ms=50.0,
                                tool_calls=1, evidence_ok=True, tools_ok=True, answer_ok=status == "pass", invention_ok=True,
                                warning_ok=True, presentation_ok=True, answer="ok", run_id="x")
    base.update(kw)
    return CaseResult(**base)


def _payload(results: list[CaseResult], **meta: Any) -> dict[str, Any]:
    return json.loads(json.dumps(results_payload({**META, **meta}, results)))


BASE = [_r("a"), _r("b"), _r("c")]


# --- 1. registro y resultados reproducibles --------------------------------------------------------------------------
def test_meta_identifies_the_run_and_the_suite_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "clave-secreta-123456789")
    m = build_meta(mode="offline", suite="all", provider="offline", model="policy", args={"x": 1}, preflight=None,
                   run_id="20261017-100000-offline-all", case_ids=["b", "a"])
    assert m["schema"] == 2 and m["run_id"] == "20261017-100000-offline-all" and m["commit_dirty"] in (True, False, None)
    assert m["suite_version"] == suite_version("all", ["a", "b"]) and m["suite_version"].startswith("all-2-")
    assert suite_version("all", ["a", "b", "c"]) != m["suite_version"]            # añadir un caso cambia la versión
    assert "clave-secreta" not in json.dumps(m)


def test_report_and_results_json_roundtrip(tmp_path: Path) -> None:
    paths = write_report(tmp_path / "run", META, [_r("a"), _r("b", "fail", detail="respuesta incorrecta")])
    data = json.loads(paths["results"].read_text(encoding="utf-8"))
    assert data["meta"]["run_id"] == "r-1" and data["summary"]["pass"] == 1 and data["summary"]["fail"] == 1
    md = paths["report"].read_text(encoding="utf-8")
    assert "Estado global: **CON FALLOS**" in md and "suite: `all-3-aaaa`" in md and "Limitaciones de la medición" in md
    assert "caché de escritura" in md and "coste monetario" in md                 # lo no disponible se declara


# --- 2. agregación sin duplicar ---------------------------------------------------------------------------------------
def test_tokens_are_summed_once_per_call_and_per_case() -> None:
    rec = RunRecord()
    rec.llm_calls = [LlmCallRecord(1, 100.0, 2000, 30, 0, 2030, False, 1, token_source="provider", cached_input_tokens=500),
                     LlmCallRecord(2, 120.0, 2600, 90, 0, 2690, False, 0, token_source="provider", paced_ms=4000.0)]
    t = rec.totals()
    assert t["input_tokens"] == 4600 and t["output_tokens"] == 120 and t["cached_input_tokens"] == 500
    assert t["llm_ms"] == 220.0 and t["paced_ms"] == 4000.0                        # la pausa no es latencia del modelo
    results = [_r("a", input_tokens=4600, llm_calls=2), _r("b", input_tokens=2000, llm_calls=1)]
    s = summarize_results(results)
    assert s["input_tokens"] == 6600 and s["llm_calls"] == 3
    cut = _r("c", "not_run", input_tokens=700, llm_calls=1, detail="infraestructura (LLM): HTTP 429")
    s2 = summarize_results([*results, cut])
    assert s2["input_tokens"] == 7300 and s2["llm_calls"] == 4 and s2["not_run"] == 1   # el consumo cortado cuenta una vez


# --- 3/4. comparación y regresión funcional --------------------------------------------------------------------------
def test_identical_runs_are_clean_and_ready() -> None:
    v = compare(_payload(BASE), _payload(BASE))
    assert v.status == "SIN_REGRESIONES" and v.ready and not v.findings
    assert "LISTA PARA REVISIÓN" in render_verdict(v, _payload(BASE), "ref.json")


def test_functional_regression_blocks_even_if_tokens_drop() -> None:
    after = [_r("a"), _r("b", "fail", detail="respuesta incorrecta", answer_ok=False, input_tokens=1000), _r("c")]
    v = compare(_payload(BASE), _payload(after))
    assert v.status == "REGRESION" and not v.ready and v.new_fails == ["b"]
    assert any(f.code == "regresion_funcional" and f.message.startswith("b:") for f in v.blocking())
    text = render_verdict(v, _payload(after), "ref.json")
    assert "NO LISTA PARA REVISIÓN" in text and "menos tokens NO compensa un fallo" in text


def test_preexisting_failure_is_not_a_regression_but_is_reported() -> None:
    before = [_r("a"), _r("b", "fail", answer_ok=False, detail="x")]
    v = compare(_payload(before), _payload(before))
    assert v.status == "SIN_REGRESIONES" and v.preexisting_fails == ["b"] and v.ready
    assert "ya existían en la referencia" in render_verdict(v, _payload(before), "r")


def test_guard_layer_degradation_is_blocking_and_presentation_is_only_a_warning() -> None:
    before = [_r("a", "fail", answer_ok=False, invention_ok=True), _r("b")]
    after = [_r("a", "fail", answer_ok=False, invention_ok=False), _r("b", presentation_ok=False)]
    v = compare(_payload(before), _payload(after))
    codes = {f.code for f in v.findings}
    assert "degradacion_invención" in codes and "degradacion_presentación" in codes
    assert v.status == "REGRESION" and {f.severity for f in v.findings if "presentación" in f.code} == {"aviso"}


# --- 5. degradación de métricas y umbrales configurables -------------------------------------------------------------
def test_metric_thresholds_are_configurable_and_comparable_only_like_for_like() -> None:
    big = [_r("a", input_tokens=7000, llm_calls=3), _r("b", input_tokens=7000, llm_calls=3), _r("c", input_tokens=7000, llm_calls=3)]
    v = compare(_payload(BASE), _payload(big))
    assert v.status == "REGRESION" and {"incremento_input_tokens", "incremento_llm_calls"} <= {f.code for f in v.findings}
    loose = compare(_payload(BASE), _payload(big), Thresholds(max_input_tokens_pct=None, max_llm_calls_pct=100.0))
    assert not any(f.severity == "bloqueante" for f in loose.findings)               # sin umbral: solo informativo
    small = [_r("a", input_tokens=5300), _r("b", input_tokens=5300), _r("c", input_tokens=5300)]   # +6 %: dentro del ruido
    assert compare(_payload(BASE), _payload(small)).status == "SIN_REGRESIONES"
    slow = [_r("a", latency_ms=9000.0), _r("b", latency_ms=9000.0), _r("c", latency_ms=9000.0)]
    assert compare(_payload(BASE), _payload(slow)).status != "REGRESION"             # la latencia no bloquea por defecto
    assert compare(_payload(BASE), _payload(slow), Thresholds(max_latency_pct=50.0)).status == "ATENCION"


def test_tokens_are_not_compared_across_estimated_and_real() -> None:
    est = [_r("a", token_source="estimated", input_tokens=9000), _r("b"), _r("c")]
    v = compare(_payload(BASE), _payload(est))
    assert any(f.code == "tokens_no_comparables" for f in v.findings) and "input_tokens" not in v.deltas
    other_mode = compare(_payload(BASE), _payload(BASE, mode="offline"))
    assert other_mode.status == "NO_COMPARABLE" and not other_mode.blocking() and not other_mode.ready
    assert "SIN REFERENCIA COMPARABLE" in render_verdict(other_mode, _payload(BASE, mode="offline"), "ref.json")


# --- 6. casos no evaluables, nuevos y ausentes ------------------------------------------------------------------------
def test_coverage_loss_new_absent_and_now_evaluable_cases() -> None:
    before = [_r("a"), _r("b"), _r("n", "not_evaluable", llm_calls=0, input_tokens=0)]
    after = [_r("a", "not_evaluable", llm_calls=0, input_tokens=0, detail="sin datos"), _r("n"), _r("z")]
    v = compare(_payload(before), _payload(after))
    codes = {f.code for f in v.findings}
    assert {"perdida_cobertura", "ahora_evaluable", "caso_nuevo", "caso_ausente"} <= codes and v.status == "REGRESION"
    fewer = compare(_payload([_r("a"), _r("b")]), _payload([_r("a"), _r("b", "not_evaluable", llm_calls=0)]),
                    Thresholds(allow_fewer_evaluable=True))
    assert any(f.code == "perdida_cobertura" for f in fewer.findings)               # perder un caso evaluado sigue avisando


def test_no_common_cases_means_no_comparison() -> None:
    v = compare(_payload([_r("a")]), _payload([_r("zz")]))
    assert v.status == "NO_COMPARABLE" and any(f.code == "sin_casos_comunes" for f in v.findings) and not v.ready


# --- 7. métricas ausentes --------------------------------------------------------------------------------------------
def test_missing_metrics_and_old_reports_do_not_crash_or_invent() -> None:
    old = _payload(BASE)
    for key in ("run_id", "suite_version", "commit_dirty", "schema"):
        old["meta"].pop(key, None)
    for c in old["cases"]:
        c.pop("tool_calls"), c.pop("cached_tokens")
    zero = [_r("a", llm_calls=0, input_tokens=0, tool_calls=0), _r("b"), _r("c")]
    v = compare(old, _payload(BASE))
    assert v.status in ("SIN_REGRESIONES", "ATENCION")
    z = compare(_payload(zero), _payload(BASE))
    assert z.deltas["input_tokens"]["before"] == 10000                              # el caso sin llamadas no aporta consumo
    md = render_markdown({k: v for k, v in META.items() if k not in ("run_id", "suite_version", "commit_dirty")}, BASE)
    assert "n/d (informe anterior a la Fase 13)" in md and "no se pudo comprobar" in md


# --- 8. ejecuciones incompletas y errores -----------------------------------------------------------------------------
def test_partial_runs_are_never_presented_as_passed() -> None:
    cut = [_r("a"), _r("b", "not_run", detail="infraestructura (LLM): HTTP 429", llm_calls=1, input_tokens=100), _r("c", "not_run",
           detail="presupuesto de 3 peticiones agotado", llm_calls=0, input_tokens=0)]
    s = summarize_results(cut)
    state, why = overall_status(s)
    assert state == "INCOMPLETA" and "no es concluyente" in why
    assert overall_status(summarize_results([_r("a")]))[0] == "SUPERADA"
    assert overall_status(summarize_results([_r("a", "not_evaluable", llm_calls=0)]))[0] == "SIN CASOS EVALUABLES"
    v = compare(_payload(BASE), _payload(cut))
    assert not v.ready and any(f.code == "errores_proveedor" for f in v.findings) and v.status == "REGRESION"
    assert "Estado global: **INCOMPLETA**" in render_markdown(META, cut)


def test_tool_errors_are_blocking() -> None:
    bad = [_r("a", tools_ok=False, status="fail", answer_ok=True, tool_issues=["la herramienta list_matches devolvió error"]),
           _r("b"), _r("c")]
    v = compare(_payload(BASE), _payload(bad))
    assert {"errores_herramientas", "regresion_funcional"} <= {f.code for f in v.blocking()}


# --- 9. saneamiento --------------------------------------------------------------------------------------------------
def test_verdict_text_masks_secrets_that_leak_into_case_details(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-entorno-987654321")
    leaky = [_r("a", "fail", answer_ok=False,
                detail="fallo con AIzaSyA1234567890abcdefghijklmnop y postgresql+psycopg://u:SuperPass99@h:5432/d y "
                       "Bearer abcdefghij123456 y clave-de-entorno-987654321"), _r("b"), _r("c")]
    v = compare(_payload(BASE), _payload(leaky))
    text = render_verdict(v, _payload(leaky), "ref.json")
    for secret in ("AIzaSyA1234567890abcdefghijklmnop", "SuperPass99", "abcdefghij123456", "clave-de-entorno-987654321"):
        assert secret not in text


# --- 10. CLI y compatibilidad -----------------------------------------------------------------------------------------
def test_cli_compare_keeps_old_output_adds_verdict_and_optional_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                         capsys: pytest.CaptureFixture[str]) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_payload([_r("a")])), encoding="utf-8")
    b.write_text(json.dumps(_payload([_r("a", "fail", answer_ok=False)])), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["evaluate", "--compare", str(a), str(b)])
    with pytest.raises(SystemExit) as exc:
        ev.main()
    out = capsys.readouterr().out
    assert exc.value.code == 0 and "REGRESIONES (pass -> fail): a" in out and "NO LISTA PARA REVISIÓN" in out   # sin cambio de código
    monkeypatch.setattr(sys, "argv", ["evaluate", "--compare", str(a), str(b), "--fail-on-regression"])
    with pytest.raises(SystemExit) as exc2:
        ev.main()
    assert exc2.value.code == 1


def test_cli_thresholds_and_list_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    big = [_r("a", input_tokens=7000)]
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(_payload([_r("a")])), encoding="utf-8")
    b.write_text(json.dumps(_payload(big)), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["evaluate", "--compare", str(a), str(b), "--fail-on-regression"])
    with pytest.raises(SystemExit) as strict:
        ev.main()
    assert strict.value.code == 1                                                   # +40 % > 15 %
    monkeypatch.setattr(sys, "argv", ["evaluate", "--compare", str(a), str(b), "--fail-on-regression", "--max-tokens-pct", "-1"])
    with pytest.raises(SystemExit) as loose:
        ev.main()
    assert loose.value.code == 0                                                    # umbral desactivado por el usuario
    capsys.readouterr()
    run = tmp_path / "hist" / "20261017-100000-live-all"
    write_report(run, META, BASE)
    (tmp_path / "hist" / "rota").mkdir()
    (tmp_path / "hist" / "rota" / "results.json").write_text("{no es json", encoding="utf-8")
    rows = list_runs(tmp_path / "hist")
    assert len(rows) == 1 and rows[0]["state"] == "SUPERADA" and rows[0]["commit"] == "abc1234"
    assert "20261017-100000-live-all" in render_runs(rows) and render_runs([]) == "No hay ejecuciones guardadas."
    monkeypatch.setattr(sys, "argv", ["evaluate", "--list-runs", "--report-dir", str(tmp_path / "hist")])
    with pytest.raises(SystemExit) as lr:
        ev.main()
    assert lr.value.code == 0 and "SUPERADA" in capsys.readouterr().out


def test_baseline_helper_writes_comparison_and_handles_missing_reference(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    ref = tmp_path / "ref.json"
    ref.write_text(json.dumps(_payload(BASE)), encoding="utf-8")
    run = tmp_path / "run"
    paths = write_report(run, META, BASE)
    ns = argparse.Namespace(baseline=str(ref), fail_on_regression=True, max_tokens_pct=None, max_calls_pct=None,
                            max_latency_pct=None, max_retries_increase=None, allow_fewer_evaluable=False)
    assert ev._compare_with_baseline(ns, paths, run) == 0
    assert "LISTA PARA REVISIÓN" in (run / "comparison.txt").read_text(encoding="utf-8")
    ns.baseline = str(tmp_path / "no-existe.json")
    assert ev._compare_with_baseline(ns, paths, run) == 0
    assert "no se inventa una" in capsys.readouterr().out
