"""Fase 1: comparación controlada legacy/compact con repeticiones. Sin LLM, sin red, sin PostgreSQL.

Comprueba el orquestador (que NO llama a nada sin --yes, que alterna los modos y fija el modo solo por entorno) y el resumen (que conserva cada
repetición por separado y distingue regresión consistente, mejora consistente y ruido)."""

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from futsal.agent.ab_summary import FILE, check_mode, load_runs, render, summarize
from futsal.agent.prompts import COMPACT, LEGACY

SPEC = importlib.util.spec_from_file_location("ab_prompt_modes", Path(__file__).parent.parent / "scripts" / "ab_prompt_modes.py")
assert SPEC and SPEC.loader
ab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ab)
SYSTEM_LEN = {LEGACY: 4124, COMPACT: 4170}


# --- orquestador ---------------------------------------------------------------------------------------------------------------------
def test_the_sixteen_cases_are_exactly_the_six_that_worsened_and_the_ten_that_improved() -> None:
    assert ab.REGRESSED == ["acta_jugador_inexistente", "circular27_comunica", "circular10_anula", "circular10_saque_meta_coloquial",
                            "tabla_goleadores_nombre_repetido", "tabla_goleadores_por_jornada_no_verificable"]
    assert len(ab.IMPROVED) == 10 and len(set(ab.CASES)) == 16 == len(ab.CASES)
    assert {"equipo_proximo_partido", "acta_goleadores_ultimo", "acta_arbitro", "regla_duracion_mitad", "combinada_lider_ultimo_resultado"} <= set(ab.IMPROVED)


def test_runs_are_three_repetitions_of_both_modes_alternating_the_order() -> None:
    assert ab.runs(3) == [("legacy", 1), ("compact", 1), ("compact", 2), ("legacy", 2), ("legacy", 3), ("compact", 3)]


def test_each_run_has_its_own_folder_and_result_and_the_same_conditions(tmp_path: Path) -> None:
    cmds = {(m, r): ab.command(m, r, tmp_path, 100, None) for m, r in ab.runs(3)}
    for (mode, rep), c in cmds.items():
        assert c[2] == "futsal.agent.evaluate" and "--confirm-live" in c and c[c.index("--only") + 1] == ",".join(ab.CASES)
        assert c[c.index("--report-dir") + 1].endswith(f"{mode}-r{rep}") and c[c.index("--output") + 1].endswith(f"{mode}-r{rep}.json")
        assert "legacy" not in " ".join(c).replace(f"{mode}-r", "") or mode == "legacy"  # el modo NO viaja en los argumentos: solo en el entorno
    same = {tuple(a for a in c if not a.startswith(str(tmp_path))) for c in cmds.values()}
    assert len(same) == 1  # idénticos salvo las rutas de salida (mismo modelo, casos, tope y configuración)
    assert "--plan" in ab.command("legacy", 1, tmp_path, 100, None, plan=True) and "--confirm-live" not in ab.command("legacy", 1, tmp_path, 100, None, plan=True)


def test_cost_estimate_in_llm_calls() -> None:
    e = ab.estimate(3, 100)
    assert e["case_runs"] == 96 and e["llm_low"] < e["llm_typical"] < e["llm_high"] <= e["hard_cap"] == 600


def test_without_yes_nothing_is_called(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("no debe lanzar procesos sin --yes")

    monkeypatch.setattr(ab.subprocess, "run", boom)
    assert ab.main([]) == 0
    out = capsys.readouterr().out
    assert "No se ha llamado a ningún LLM" in out and "AGENT_PROMPT_MODE=<modo>" in out and "legacy r1, compact r1, compact r2, legacy r2, legacy r3, compact r3" in out and "tope duro 600" in out


def test_yes_runs_each_batch_with_the_mode_in_the_environment_and_resumes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], str | None]] = []

    def fake(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, (kw.get("env") or {}).get("AGENT_PROMPT_MODE")))
        if "--output" in cmd:
            Path(cmd[cmd.index("--output") + 1]).write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ab.subprocess, "run", fake)
    assert ab.main(["--yes", "--out", str(tmp_path)]) == 0
    evals = [(c, m) for c, m in calls if "futsal.agent.evaluate" in c]
    assert [m for _, m in evals] == ["legacy", "compact", "compact", "legacy", "legacy", "compact"] and len(evals) == 6
    assert any("futsal.agent.ab_summary" in c for c, _ in calls) and (tmp_path / "manifest.json").exists()
    calls.clear()
    assert ab.main(["--yes", "--out", str(tmp_path)]) == 0
    assert not [c for c, _ in calls if "futsal.agent.evaluate" in c]  # ya existen las 6: se reanuda sin gastar nada
    calls.clear()
    assert ab.main(["--yes", "--force", "--out", str(tmp_path)]) == 0 and len([c for c, _ in calls if "futsal.agent.evaluate" in c]) == 6


def test_a_batch_that_cannot_run_stops_the_whole_comparison(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def fake(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 2, "", "")

    monkeypatch.setattr(ab.subprocess, "run", fake)
    assert ab.main(["--yes", "--out", str(tmp_path)]) == 2
    assert len([c for c in seen if "futsal.agent.evaluate" in c]) == 1  # no se gasta cuota en las demás


# --- resumen ---------------------------------------------------------------------------------------------------------------------------
def case(cid: str, status: str, tools: list[str] | None = None, llm: int = 3, mcp: int = 2, inp: int = 9000, out: int = 120, **extra: Any) -> dict[str, Any]:
    return {"case_id": cid, "status": status, "tools_used": tools or ["list_matches"], "llm_calls": llm, "tool_calls": mcp, "input_tokens": inp,
            "output_tokens": out, "cached_tokens": 0, "token_source": "provider", **extra}


def doc(mode: str, cases: list[dict[str, Any]], run: str = "r") -> dict[str, Any]:
    return {"meta": {"run_id": run, "commit": "abc", "model": "gemini-3.5-flash-lite"}, "summary": {"prefix_chars": {"system_prompt": SYSTEM_LEN[mode]}}, "cases": cases}


def write(folder: Path, name: str, d: dict[str, Any]) -> None:
    (folder / name).write_text(json.dumps(d), encoding="utf-8")


def build(folder: Path) -> None:
    plan = {  # caso: (legacy r1..r3, compact r1..r3)
        "regresion": ("pass pass pass", "fail fail pass"), "mejora": ("fail fail pass", "pass pass pass"),
        "ruido": ("pass fail pass", "pass pass fail"), "igual": ("pass pass pass", "pass pass pass"), "nada": ("not_evaluable " * 3, "not_evaluable " * 3)}
    for mode, col in ((LEGACY, 0), (COMPACT, 1)):
        for rep in range(3):
            cs = [case(cid, v[col].split()[rep], tools=["get_scorer_table"] if (cid == "regresion" and mode == COMPACT) else None,
                       inp=9000 if mode == LEGACY else 8800,
                       **({"detail": "respuesta incorrecta", "tool_issues": ["herramienta no esperada get_scorer_table"], "evidence_ok": False}
                          if v[col].split()[rep] == "fail" else {}))
                  for cid, v in plan.items()]
            write(folder, f"{mode}-r{rep + 1}.json", doc(mode, cs, f"{mode}{rep + 1}"))


def test_summary_keeps_every_repetition_and_classifies_each_case(tmp_path: Path) -> None:
    build(tmp_path)
    (tmp_path / "otro.json").write_text("{}", encoding="utf-8")  # ficheros ajenos se ignoran
    s = summarize(load_runs(tmp_path), SYSTEM_LEN)
    by = {c["case"]: c for c in s["cases"]}
    assert {k: v["verdict"] for k, v in by.items()} == {"regresion": "regresión consistente", "mejora": "mejora consistente", "ruido": "variable (ruido)",
                                                       "igual": "igual", "nada": "sin datos"}
    reg = by["regresion"]["modes"]
    assert sorted(reg[COMPACT]["reps"]) == ["1", "2", "3"] and [r["status"] for r in reg[COMPACT]["reps"].values()] == ["fail", "fail", "pass"]
    assert reg[LEGACY]["passes"] == 3 and reg[COMPACT]["passes"] == 1 and reg[COMPACT]["flaky"] and not reg[LEGACY]["flaky"]
    assert reg[COMPACT]["tool_sets"] == ["get_scorer_table"] and reg[LEGACY]["tool_sets"] == ["list_matches"] and "capa datos" in reg[COMPACT]["reasons"]
    assert "herramienta no esperada get_scorer_table" in " ".join(reg[COMPACT]["reasons"]) and "respuesta incorrecta" in reg[COMPACT]["reasons"]
    assert s["verdicts"]["regresión consistente"] == 1 and s["verdicts"]["mejora consistente"] == 1 and not s["warnings"]


def test_variability_and_tokens_are_reported_per_mode_and_repetition(tmp_path: Path) -> None:
    build(tmp_path)
    s = summarize(load_runs(tmp_path), SYSTEM_LEN)
    assert s["totals"][LEGACY]["flaky_cases"] == 2 and s["totals"][COMPACT]["flaky_cases"] == 2  # mejora y ruido en legacy; regresión y ruido en compact
    r1 = s["totals"][LEGACY]["runs"]["1"]
    assert r1["decisive"] == 4 and r1["llm_calls"] == 12 and r1["mcp_calls"] == 8 and r1["input_tokens"] == 36000 and r1["tokens_all_from_provider"]
    assert s["totals"][COMPACT]["runs"]["1"]["input_per_llm_call"] < r1["input_per_llm_call"]
    text = render(s)
    assert "| legacy | 1 |" in text and "| compact | 3 |" in text and "### `regresion` — regresión consistente" in text and "P P P (3/3)" in text and "F F P (1/3)" in text


def test_estimated_tokens_are_never_passed_off_as_real(tmp_path: Path) -> None:
    for mode in (LEGACY, COMPACT):
        write(tmp_path, f"{mode}-r1.json", doc(mode, [case("a", "pass", token_source="estimated")]))
    s = summarize(load_runs(tmp_path), SYSTEM_LEN)
    assert not s["totals"][LEGACY]["runs"]["1"]["tokens_all_from_provider"] and s["totals"][LEGACY]["runs"]["1"]["input_tokens"] == 0
    assert "¡estimados!" in render(s)


def test_a_run_made_in_the_wrong_mode_is_flagged(tmp_path: Path) -> None:
    write(tmp_path, "compact-r1.json", doc(LEGACY, [case("a", "pass")]))  # el system prompt es el de legacy: el entorno no se aplicó
    write(tmp_path, "legacy-r1.json", doc(LEGACY, [case("a", "pass")]))
    s = summarize(load_runs(tmp_path), SYSTEM_LEN)
    assert len(s["warnings"]) == 1 and "compact" in s["warnings"][0] and "AGENT_PROMPT_MODE" in s["warnings"][0]
    assert check_mode(LEGACY, doc(LEGACY, []), SYSTEM_LEN) is None and check_mode(COMPACT, {"summary": {}}, SYSTEM_LEN) is None


def test_file_pattern_only_accepts_the_orchestrator_names() -> None:
    assert FILE.match("legacy-r1.json") and FILE.match("compact-r12.json") and not FILE.match("eval-legacy.json") and not FILE.match("summary.json")
