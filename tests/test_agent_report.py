"""Informe, comparación, plan de cuota, puerta online y saneado de trazas: sin red, sin BD, sin LLM."""

import json
import sys
from pathlib import Path

import pytest

from futsal.agent import evaluate as ev
from futsal.agent.evaluate import CaseResult, live_gate
from futsal.agent.report import compare_runs, plan_estimate, render_markdown, render_plan, results_payload
from futsal.agent.trace import sanitize_trace_file, sanitize_text

META = {"generated_at": "2026-10-13T10:00:00+00:00", "mode": "live", "suite": "smoke", "provider": "gemini",
        "model": "m", "commit": "abc1234", "args": {}, "preflight": {"same_database": "same", "detail": "huella ok"}}


def _r(cid: str, status: str = "pass", **kw: object) -> CaseResult:
    base = dict(case_id=cid, category="x", question="¿q?", status=status, criteria="menciona algo", llm_calls=2,
                input_tokens=1000, output_tokens=50, token_source="provider", latency_ms=1200.0, tool_ms=80.0,
                evidence_ok=True, tools_ok=True, answer_ok=True, invention_ok=True, warning_ok=True,
                presentation_ok=True, tools_used=["list_matches"], tool_trace=["list_matches(team_id=11)"],
                answer="Quedó 3-4.", run_id="r1")
    base.update(kw)
    return CaseResult(**base)  # type: ignore[arg-type]


def test_markdown_report_has_layers_criteria_answer_and_token_origin() -> None:
    results = [_r("a"), _r("b", "fail", invention_ok=False, claims=["nombra a «X» sin respaldo"], detail="invención: x"),
               _r("c", "not_evaluable", detail="requiere LLM real", llm_calls=0, answer="")]
    md = render_markdown(META, results)
    assert "| invención | 1 | 1 |" in md and "REALES (informados por el proveedor)" in md
    assert "**Criterio de corrección:** menciona algo" in md and "> Quedó 3-4." in md
    assert "list_matches(team_id=11)" in md and "requiere LLM real" in md and "huella ok" in md
    assert "commit: `abc1234`" in md and "1 pass · 1 fail · 1 no evaluables" in md


def test_estimated_tokens_are_labeled_as_estimates_not_real() -> None:
    md = render_markdown({**META, "mode": "offline"}, [_r("a", token_source="estimated", tokens_estimated=True)])
    assert "ESTIMADOS localmente" in md and "REALES" not in md


def test_compare_flags_regressions_and_incomparable_runs() -> None:
    before = results_payload({**META, "mode": "offline", "model": "policy"}, [_r("a"), _r("b", input_tokens=900)])
    after = results_payload(META, [_r("a", "fail"), _r("b", input_tokens=700), _r("n")])
    out = compare_runs(before, after)
    assert "REGRESIONES (pass -> fail): a" in out and "(nuevo)" in out and "-200" in out
    assert "no son comparables en tokens" in out  # offline estimado vs live real: no se mezclan


def test_plan_estimate_separates_exact_limits_from_estimates() -> None:
    off = [_r("a", llm_calls=2, input_tokens=3000), _r("b", llm_calls=3, input_tokens=5000),
           _r("c", "not_evaluable", llm_calls=0, input_tokens=0, detail="sin datos")]
    p = plan_estimate(off, selected=3, max_tool_calls=5, retries=2, max_requests=40)
    assert (p["typical_llm_calls"], p["max_llm_calls"], p["max_http_requests_worst_case"]) == (5, 12, 36)
    assert p["estimated_input_tokens"] == 8000 and p["not_evaluable"] == ["c: sin datos"]
    txt = render_plan(p)
    assert "ESTIMADOS" in txt and "PEOR caso" in txt and "--max-requests" in txt and "peor caso" not in txt.lower().split("[")[0]


@pytest.mark.parametrize(("verdict", "allow", "ok"), [("same", False, True), ("different", False, False),
                                                      ("unproven", False, False), ("unproven", True, True),
                                                      ("different", True, True)])
def test_live_gate_blocks_unless_same_database_is_proven(verdict: str, allow: bool, ok: bool) -> None:
    allowed, msg = live_gate(verdict, allow)
    assert allowed is ok
    if not ok:
        assert "BLOQUEADA" in msg and "preflight" in msg
    elif verdict != "same":
        assert "AVISO" in msg


def test_sanitize_masks_secrets_connections_and_drops_tool_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-entorno-123456")
    src = tmp_path / "raw.jsonl"
    rows = [{"run_id": "r1", "question": "¿q?", "answer": "ok AIzaSyA1234567890abcdefghijklmnop y clave-de-entorno-123456",
             "error": "fallo con postgresql+psycopg://futsal:SuperPass99@localhost:5432/futsal y Bearer abcdefghij123456",
             "tool_calls": [{"name": "list_matches", "arguments": {"api_key": "x"}, "result": "DATOS GRANDES",
                             "result_summary": "13 matches"}],
             "long": "x" * 5000}]
    src.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    dst = tmp_path / "out" / "san.jsonl"
    assert sanitize_trace_file(src, dst) == 1
    raw = dst.read_text(encoding="utf-8")
    for secret in ("AIzaSyA1234567890abcdefghijklmnop", "clave-de-entorno-123456", "SuperPass99", "abcdefghij123456",
                   "DATOS GRANDES"):
        assert secret not in raw
    row = json.loads(raw)
    assert "result" not in row["tool_calls"][0] and row["tool_calls"][0]["result_summary"] == "13 matches"
    assert row["run_id"] == "r1" and len(row["long"]) < 1600 and "recortado" in row["long"]
    assert sanitize_text("password=hunter2xx") == "password=***"


def test_cli_compare_and_sanitize_need_no_database_or_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(results_payload(META, [_r("a")])), encoding="utf-8")
    b.write_text(json.dumps(results_payload(META, [_r("a", "fail")])), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["evaluate", "--compare", str(a), str(b)])
    with pytest.raises(SystemExit) as exc:
        ev.main()
    assert exc.value.code == 0 and "REGRESIONES (pass -> fail): a" in capsys.readouterr().out
    raw, out = tmp_path / "r.jsonl", tmp_path / "o.jsonl"
    raw.write_text('{"question":"q","tool_calls":[{"result":"X"}]}\n', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["evaluate", "--sanitize-traces", str(raw), str(out)])
    with pytest.raises(SystemExit):
        ev.main()
    assert "1 ejecuciones saneadas" in capsys.readouterr().out and '"result"' not in out.read_text(encoding="utf-8")
