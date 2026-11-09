"""`regrade`: recalifica una ejecución de regression ya hecha sin llamar al LLM, en un directorio nuevo y sin tocar el original."""

import hashlib
import json
import socket
from pathlib import Path
from typing import Any

import pytest
from test_evals_regression import LegacyScript

from futsal.agent.evaluate import build_cases, load_truth
from futsal.evals import cli
from futsal.evals import report as reporting
from futsal.evals.runner import RunConfig, _record, run_eval, write_outputs

FALSE_POSITIVE = "El Reglamento ya recoge este cambio (Circular n.º 27 de la RFEF, páginas 1-2)."
REAL_INVENTION = "El partido acabó 4-3 según el acta."
CLAIM = "invención: da el marcador {}, que no figura en lo devuelto por las herramientas"


def _critical(cid: str, answer: str, score: str) -> dict[str, Any]:
    legacy = {"status": "fail", "evidence_ok": True, "tools_ok": True, "answer_ok": True, "invention_ok": False,
              "warning_ok": True, "presentation_ok": True}
    return _record(cid, "G", False, 0, 0, "pregunta", "critical", CLAIM.format(score), error_class="critical", answer=answer,
                   tools_used=["search_rules"], grade={"facts_present": ["answer_ok"], "tools_missing": [], "tools_unexpected": [], "over_budget": False, "legacy": legacy})


def _correct(cid: str) -> dict[str, Any]:
    legacy = {"status": "pass", "evidence_ok": True, "tools_ok": True, "answer_ok": True, "invention_ok": True,
              "warning_ok": True, "presentation_ok": True}
    return _record(cid, "G", False, 0, 0, "pregunta", "correct", answer="Respuesta correcta.", tools_used=["x"],
                   grade={"facts_present": ["answer_ok"], "tools_missing": [], "tools_unexpected": [], "over_budget": False, "legacy": legacy})


def _write_run(path: Path) -> dict[str, Any]:
    records = [_critical("circular", FALSE_POSITIVE, "1-2"), _critical("inventado", REAL_INVENTION, "4-3"), _correct("ok")]
    results: dict[str, Any] = {
        "schema": 1, "records": records,
        "meta": {"suite": "regression", "split": "dev", "arm": "legacy", "commit": "c" * 40, "reps": 1, "generated_at": "2026-11-09T00:00:00Z",
                 "model": {"provider": "scripted", "model": "m"}, "budget": {"used": 0, "max_requests": 1}, "stopped": None,
                 "agent_flags": {}, "temperature": 0, "priced": False, "prices": {"model": "m", "note": "sin precio"}, "clock": {"as_of": "x", "dependencies": []},
                 "dataset": {"cases": 3, "evaluable": 3},
                 "snapshot": {"id": "snap", "as_of": "2026-11-09T00:00:00+01:00", "fingerprint": "f" * 64, "synthetic": True}},
        "cases": {r["case_id"]: {"stratum": "G", "holdout": False, "capability": "v1", "status": "ok", "reason": "", "bindings": {},
                                 "expected": [{"user": "pregunta", "behavior": "answer", "tools_needed": []}]} for r in records}}
    results["summary"] = reporting.summarize(results)
    write_outputs(path, results)
    return results


def _digest(path: Path) -> dict[str, str]:
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.rglob("*")) if p.is_file()}


@pytest.fixture
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("regrade no puede llamar al LLM ni a la red")

    monkeypatch.setattr("futsal.agent.llm.create_llm", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)


def test_regrade_without_evidence_fixes_only_the_false_positive(tmp_path: Path, no_llm: None) -> None:
    run = tmp_path / "run"
    _write_run(run)
    before = _digest(run)
    assert cli.main(["regrade", "--run", str(run), "--snapshot", "snap"]) == 0
    assert _digest(run) == before  # el original queda intacto byte a byte
    out = tmp_path / "run-regraded"
    new = json.loads((out / "results.json").read_text(encoding="utf-8"))
    by = {r["case_id"]: r for r in new["records"]}
    assert by["circular"]["verdict"] == "correct" and by["circular"]["regraded_partial"] is True
    assert by["circular"]["regraded_partial_reason"] and by["circular"]["grade"]["legacy"]["invention_ok"] is True
    assert by["inventado"]["verdict"] == "critical" and "regraded_partial" not in by["inventado"]
    assert by["ok"]["verdict"] == "correct"
    diff = json.loads((out / "verdict-diff.json").read_text(encoding="utf-8"))
    assert [(d["case"], d["before"], d["after"]) for d in diff] == [("circular", "critical", "correct")]
    acc = new["summary"]["global"]["accuracy"]
    assert (acc["k"], acc["n"]) == (2, 3) and new["meta"]["regrade"]["llm_calls"] == 0


def test_regrade_keeps_other_failing_layers(tmp_path: Path, no_llm: None) -> None:
    run = tmp_path / "run"
    results = _write_run(run)
    rec = results["records"][0]
    rec["grade"]["legacy"]["tools_ok"] = False
    write_outputs(run, results)
    assert cli.main(["regrade", "--run", str(run), "--snapshot", "snap", "--output", str(tmp_path / "otro")]) == 0
    new = json.loads((tmp_path / "otro" / "results.json").read_text(encoding="utf-8"))
    circ = next(r for r in new["records"] if r["case_id"] == "circular")
    assert circ["verdict"] == "fail" and circ["error_class"] == "interpretation" and "herramientas" in circ["reason"]


def test_regrade_never_overwrites_and_checks_snapshot(tmp_path: Path, no_llm: None) -> None:
    run = tmp_path / "run"
    _write_run(run)
    (tmp_path / "run-regraded").mkdir()
    assert cli.main(["regrade", "--run", str(run), "--snapshot", "snap"]) == 1
    assert cli.main(["regrade", "--run", str(run), "--snapshot", "otro", "--output", str(tmp_path / "n")]) == 1


def test_regression_saves_evidence_and_regrade_uses_it(synthetic: Any, tmp_path: Path, no_llm: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # la ejecución real (con guion, sin red) sí necesita sockets locales
    engine = synthetic.engine
    truth = load_truth(engine)
    assert truth is not None
    cases = build_cases(truth)
    cfg = RunConfig(suite="regression", snapshot="synthetic-dev", output=tmp_path / "reg", root=synthetic.root, db_url=synthetic.url,
                    max_requests=5000, reps=1)
    results = run_eval(cfg, lambda: LegacyScript(cases))
    with_tools = [r for r in results["records"] if r["tools_used"]]
    assert with_tools and any(r["evidence"] for r in with_tools)  # las llamadas rechazadas o con error no dejan payload (igual que en el evaluador)
    assert all(isinstance(p, dict) for r in with_tools for p in r["evidence"])
    before = _digest(tmp_path / "reg")
    assert cli.main(["regrade", "--run", str(tmp_path / "reg"), "--snapshot", "synthetic-dev", "--db", synthetic.url,
                     "--root", str(synthetic.root)]) == 0
    assert _digest(tmp_path / "reg") == before
    new = json.loads((tmp_path / "reg-regraded" / "results.json").read_text(encoding="utf-8"))
    assert [r["verdict"] for r in new["records"]] == [r["verdict"] for r in results["records"]]  # con la misma evidencia nada cambia
    assert new["meta"]["regrade"]["llm_calls"] == 0
