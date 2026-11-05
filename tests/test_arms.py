"""Fase E0: brazos de evaluación, orquestador de N brazos y resumen. Sin LLM, sin red, sin PostgreSQL.

Incluye la comprobación de compatibilidad con la comparación legacy/compact de la Fase 1: `ab_summary` y `ab_prompt_modes` NO se han modificado y su
salida sigue idéntica al fixture oro; el resumen de N brazos coincide con él en los casos legacy/compact."""

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import test_ab_summary as ab_tests  # noqa: E402  (helpers de datos sintéticos de la Fase 1)
from futsal.agent import arms, arms_summary
from futsal.agent.ab_summary import load_runs, render, summarize
from futsal.agent.report import build_meta

HERE = Path(__file__).parent
GOLDEN = json.loads((HERE / "fixtures" / "ab_summary_golden.json").read_text(encoding="utf-8"))
SPEC = importlib.util.spec_from_file_location("ab_arms", HERE.parent / "scripts" / "ab_arms.py")
assert SPEC and SPEC.loader
ab_arms = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ab_arms)


# --- brazos y banderas ---------------------------------------------------------------------------------------------------------------
def test_every_arm_fixes_all_flags_and_never_inherits_the_users_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_PREFETCH", "on")
    monkeypatch.setenv("AGENT_PROMPT_MODE", "compact")
    for name in arms.ARMS:
        env = arms.arm_env(name)
        assert set(env) == set(arms.FLAGS) and env["AGENT_TELEMETRY"] == "off"  # una evaluación nunca escribe telemetría de uso real
    assert arms.arm_env("legacy") == {"AGENT_PROMPT_MODE": "legacy", "AGENT_PREFETCH": "off", "AGENT_TELEMETRY": "off", "AGENT_ROUTER": "shadow"}
    assert arms.arm_env("compact_prefetch")["AGENT_PROMPT_MODE"] == "compact" and arms.arm_env("compact_prefetch")["AGENT_PREFETCH"] == "on"


def test_unknown_arm_or_flag_is_rejected() -> None:
    with pytest.raises(ValueError):
        arms.arm_env("inventado")
    with pytest.raises(ValueError):
        arms.validate_flags({"AGENT_PREFETCH": "quizás"})
    with pytest.raises(ValueError):
        arms.validate_flags({"AGENT_OTRA": "on"})


def test_defaults_keep_every_experimental_feature_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in arms.FLAGS:
        monkeypatch.delenv(key, raising=False)
    assert arms.DEFAULTS == {"AGENT_PROMPT_MODE": "legacy", "AGENT_PREFETCH": "off", "AGENT_TELEMETRY": "off", "AGENT_ROUTER": "shadow"}
    assert arms.current_flags() == arms.DEFAULTS
    monkeypatch.setenv("AGENT_PREFETCH", "ON")
    monkeypatch.setenv("AGENT_PROMPT_MODE", "raro")
    assert arms.current_flags()["AGENT_PREFETCH"] == "on" and arms.current_flags()["AGENT_PROMPT_MODE"] == "legacy"


def test_every_evaluation_records_the_flags_it_ran_with() -> None:
    meta = build_meta(mode="offline", suite="all", provider="x", model="y", args={}, preflight=None)
    assert meta["agent_flags"] == arms.current_flags()


def test_presets_are_the_approved_case_lists() -> None:
    assert len(arms.PRESETS["phase1"]) == 16 == len(set(arms.PRESETS["phase1"]))
    probe = arms.PRESETS["prefetch_probe"]
    assert len(probe) == 12 == len(set(probe)) and {"equipo_resultados", "enfrentamiento_ganador", "fuera_de_alcance", "equipo_inexistente"} <= set(probe)


# --- orquestador ---------------------------------------------------------------------------------------------------------------------
def test_schedule_rotates_the_order_of_arms() -> None:
    assert ab_arms.schedule(["legacy", "prefetch"], 3) == [("legacy", 1), ("prefetch", 1), ("prefetch", 2), ("legacy", 2), ("legacy", 3), ("prefetch", 3)]
    three = ab_arms.schedule(["a", "b", "c"], 3)
    assert [x[0] for x in three[:3]] == ["a", "b", "c"] and [x[0] for x in three[3:6]] == ["b", "c", "a"] and [x[0] for x in three[6:]] == ["c", "a", "b"]


def test_plan_shows_global_maxima_and_separates_estimates_from_measurements(capsys: pytest.CaptureFixture[str]) -> None:
    assert ab_arms.main(["--arms", "legacy,prefetch"]) == 0
    out = capsys.readouterr().out
    assert "Tandas: 6" in out and "ejecuciones de caso (máximo global): 72" in out and "TOPE DURO de peticiones al LLM (se aplica): 600" in out
    assert "ESTIMADAS" in out and "MEDIDO en tu ejecución anterior" in out and "cota teórica de diseño (no estimada): 504" in out
    assert "Coste: n/d" in out and "No se ha llamado a ningún LLM" in out and "USD" not in out  # sin tarifa verificada no hay coste


def test_cost_only_with_a_fully_sourced_price(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        ab_arms.main(["--arms", "legacy,prefetch", "--price-in", "0.1", "--price-out", "0.4"])  # sin fuente ni fecha: no es una tarifa verificada
    with pytest.raises(SystemExit):
        ab_arms.main(["--arms", "legacy,prefetch", "--price-in", "0.1"])
    assert ab_arms.main(["--arms", "legacy,prefetch", "--price-in", "0.1", "--price-out", "0.4", "--price-source", "doc del proveedor", "--price-date", "2026-11-02"]) == 0
    out = capsys.readouterr().out
    assert "ESTIMACIÓN con la tarifa que aportas: doc del proveedor, verificada el 2026-11-02" in out and "n/d" not in out.split("Coste:")[1]


def test_plan_numbers_arithmetic() -> None:
    n = ab_arms.plan_numbers(2, 3, 12, 100, 4626, 49, None, None)
    assert n["case_runs"] == 72 and n["calls_typical"] == round(72 * 3.2) and n["calls_design_bound"] == 72 * 7 and n["hard_cap_requests"] == 600
    assert n["input_tokens"] == round(n["calls_typical"] * 4626) and "cost_usd" not in n  # type: ignore[operator]
    assert ab_arms.plan_numbers(2, 3, 12, 100, 4626, 49, 1.0, 2.0)["cost_usd"] > 0
    assert ab_arms.plan_numbers(2, 3, None, 100, 4626, 49, None, None)["case_runs"] is None  # suite entera: lo calcula el evaluador


def test_nothing_runs_without_yes_and_each_batch_gets_its_arms_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kw.get("env") or {}))
        if "--output" in cmd:
            Path(cmd[cmd.index("--output") + 1]).write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ab_arms.subprocess, "run", fake)
    assert ab_arms.main(["--arms", "legacy,prefetch", "--out", str(tmp_path)]) == 0 and not calls  # sin --yes: ninguna llamada
    monkeypatch.setenv("AGENT_TELEMETRY", "on")  # el entorno del usuario no se hereda
    assert ab_arms.main(["--arms", "legacy,prefetch", "--yes", "--reps", "1", "--out", str(tmp_path)]) == 0
    runs = [(c, e) for c, e in calls if "futsal.agent.evaluate" in c]
    assert [e["AGENT_PREFETCH"] for _, e in runs] == ["off", "on"] and all(e["AGENT_TELEMETRY"] == "off" and e["AGENT_PROMPT_MODE"] == "legacy" for _, e in runs)
    assert all("--confirm-live" in c and "--max-requests" in c for c, _ in runs)
    assert any("futsal.agent.arms_summary" in c for c, _ in calls) and (tmp_path / "manifest.json").exists()
    calls.clear()
    assert ab_arms.main(["--arms", "legacy,prefetch", "--yes", "--reps", "1", "--out", str(tmp_path)]) == 0
    assert not [c for c, _ in calls if "futsal.agent.evaluate" in c]  # reanuda sin gastar


def test_a_batch_that_cannot_run_stops_everything(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def fake(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 2, "", "")

    monkeypatch.setattr(ab_arms.subprocess, "run", fake)
    assert ab_arms.main(["--arms", "legacy,prefetch", "--yes", "--out", str(tmp_path)]) == 2
    assert len([c for c in seen if "futsal.agent.evaluate" in c]) == 1


# --- resumen de N brazos -------------------------------------------------------------------------------------------------------------
def doc(arm: str, cases: list[dict[str, Any]], flags: bool = True) -> dict[str, Any]:
    d = ab_tests.doc("legacy", cases)
    if flags:
        d["meta"]["agent_flags"] = arms.arm_env(arm)
    return d


def three_arms(folder: Path) -> None:
    plan = {"igual": ("pass pass pass", "pass pass pass", "pass pass pass"), "regresion_prefetch": ("pass pass pass", "pass pass pass", "fail fail pass"),
            "mejora_compact": ("fail fail pass", "pass pass pass", "fail fail pass")}
    for col, arm in enumerate(("legacy", "compact", "prefetch")):
        for rep in range(3):
            cs = [ab_tests.case(cid, v[col].split()[rep], inp=9000 - 200 * col) for cid, v in plan.items()]
            (folder / f"{arm}-r{rep + 1}.json").write_text(json.dumps(doc(arm, cs)), encoding="utf-8")


def test_n_arm_summary_compares_every_arm_with_the_baseline(tmp_path: Path) -> None:
    three_arms(tmp_path)
    s = arms_summary.summarize(arms_summary.load_arms(tmp_path))
    assert s["baseline"] == "legacy" and s["arms"] == ["legacy", "compact", "prefetch"] and not s["warnings"]
    by = {c["case"]: c["verdicts"] for c in s["cases"]}
    assert by["igual"] == {"compact": "igual", "prefetch": "igual"}
    assert by["regresion_prefetch"] == {"compact": "igual", "prefetch": "regresión consistente"}
    assert by["mejora_compact"] == {"compact": "mejora consistente", "prefetch": "variable (ruido)"}
    assert s["verdicts"]["prefetch"]["regresión consistente"] == 1 and s["totals"]["legacy"]["runs"]["1"]["pass"] == 2
    text = arms_summary.render(s)
    assert "MEDIDO" in text and "Nada de este informe es simulado" in text and "| legacy | 1 |" in text and "### `regresion_prefetch`" in text


def test_a_run_with_the_wrong_or_missing_flags_is_flagged(tmp_path: Path) -> None:
    three_arms(tmp_path)
    wrong = doc("legacy", [ab_tests.case("a", "pass")])  # banderas de legacy dentro del brazo prefetch
    (tmp_path / "prefetch-r1.json").write_text(json.dumps(wrong), encoding="utf-8")
    old = doc("legacy", [ab_tests.case("a", "pass")], flags=False)
    (tmp_path / "compact-r1.json").write_text(json.dumps(old), encoding="utf-8")
    warnings = arms_summary.summarize(arms_summary.load_arms(tmp_path))["warnings"]
    assert any("prefetch" in w and "AGENT_PREFETCH" in w for w in warnings) and any("compact" in w and "no registra agent_flags" in w for w in warnings)


def test_estimated_tokens_are_never_passed_off_as_real(tmp_path: Path) -> None:
    for arm in ("legacy", "prefetch"):
        (tmp_path / f"{arm}-r1.json").write_text(json.dumps(doc(arm, [ab_tests.case("a", "pass", token_source="estimated")])), encoding="utf-8")
    s = arms_summary.summarize(arms_summary.load_arms(tmp_path))
    assert not s["totals"]["legacy"]["runs"]["1"]["tokens_all_from_provider"] and "¡estimados!" in arms_summary.render(s)


# --- compatibilidad con la comparación legacy/compact de la Fase 1 --------------------------------------------------------------------
def test_phase1_ab_summary_output_is_unchanged(tmp_path: Path) -> None:
    ab_tests.build(tmp_path)
    s = summarize(load_runs(tmp_path), ab_tests.SYSTEM_LEN)
    assert json.loads(json.dumps(s)) == GOLDEN["summary"] and render(s) == GOLDEN["render"]


def test_phase1_orchestrator_still_builds_the_same_batches() -> None:
    spec = importlib.util.spec_from_file_location("ab_prompt_modes", HERE.parent / "scripts" / "ab_prompt_modes.py")
    assert spec and spec.loader
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    assert old.runs(3) == [("legacy", 1), ("compact", 1), ("compact", 2), ("legacy", 2), ("legacy", 3), ("compact", 3)]
    assert old.CASES == arms.PRESETS["phase1"] and old.estimate(3, 100)["hard_cap"] == 600


def test_n_arm_summary_agrees_with_the_phase1_summary_on_legacy_and_compact(tmp_path: Path) -> None:
    ab_tests.build(tmp_path)
    old = summarize(load_runs(tmp_path), ab_tests.SYSTEM_LEN)
    new = arms_summary.summarize(arms_summary.load_arms(tmp_path))
    assert {c["case"]: c["verdict"] for c in old["cases"]} == {c["case"]: c["verdicts"]["compact"] for c in new["cases"]}
    for c_old, c_new in zip(old["cases"], new["cases"], strict=True):
        for mode in ("legacy", "compact"):
            assert c_old["modes"][mode]["passes"] == c_new["arms"][mode]["passes"] and c_old["modes"][mode]["flaky"] == c_new["arms"][mode]["flaky"]


# --- completar casos sin ejecutar y veredictos con pocas repeticiones -----------------------------------------------------------------
def test_few_decisive_runs_are_never_called_consistent_and_429s_are_flagged(tmp_path: Path) -> None:
    runs = {"legacy": ["pass", "pass", "not_run"], "prefetch": ["not_run", "pass", "fail"]}
    for arm, sts in runs.items():
        for rep, st in enumerate(sts, 1):
            extra = {"detail": "infraestructura (LLM): 429 RESOURCE_EXHAUSTED"} if st == "not_run" else {}
            (tmp_path / f"{arm}-r{rep}.json").write_text(json.dumps(doc(arm, [ab_tests.case("c", st, **extra)])), encoding="utf-8")
    s = arms_summary.summarize(arms_summary.load_arms(tmp_path))
    assert s["cases"][0]["verdicts"]["prefetch"].startswith("insuficiente") and s["verdicts"]["prefetch"]["regresión consistente"] == 0
    text = arms_summary.render(s)
    assert "429×1" in text and "--fill-missing" in text


def test_fill_missing_reruns_only_not_run_cases_and_merges(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "legacy-r1.json").write_text(json.dumps(doc("legacy", [ab_tests.case("a", "pass"), ab_tests.case("b", "not_run")])), encoding="utf-8")
    (tmp_path / "prefetch-r1.json").write_text(json.dumps(doc("prefetch", [ab_tests.case("a", "pass")])), encoding="utf-8")
    seen: list[tuple[list[str], dict[str, str]]] = []

    def fake(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append((cmd, kw.get("env") or {}))
        if "--output" in cmd:
            Path(cmd[cmd.index("--output") + 1]).write_text(json.dumps(doc("legacy", [ab_tests.case("b", "pass")])), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ab_arms.subprocess, "run", fake)
    assert ab_arms.main(["--arms", "legacy,prefetch", "--reps", "1", "--yes", "--fill-missing", "--min-interval", "8", "--out", str(tmp_path)]) == 0
    runs = [(c, e) for c, e in seen if "futsal.agent.evaluate" in c]
    assert len(runs) == 1 and runs[0][0][runs[0][0].index("--only") + 1] == "b" and runs[0][1]["AGENT_PREFETCH"] == "off" and "8.0" in runs[0][0]
    merged = json.loads((tmp_path / "legacy-r1.json").read_text(encoding="utf-8"))
    assert [(c["case_id"], c["status"]) for c in merged["cases"]] == [("a", "pass"), ("b", "pass")] and merged["cases"][1]["filled"]
    assert (tmp_path / "legacy-r1.orig.json").exists() and not (tmp_path / "legacy-r1-fill.json").exists()
