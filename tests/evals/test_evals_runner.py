"""Runner extremo a extremo con un LLM simulado de guion fijo (sin red ni LLM real) sobre el snapshot sintético."""

import json
from pathlib import Path
from typing import Any

import pytest
from world import ScriptedLLM, World, build_world, normalize, oracle_script

from futsal.agent.llm import QUOTA_MARKER, LLMQuotaError
from futsal.evals import snapshot as snaps
from futsal.evals.datasets import SealError, load_cases, suite_cases
from futsal.evals.runner import FingerprintMismatch, RunConfig, rotated, run_eval

SNAP = "synthetic-dev"


@pytest.fixture(scope="module")
def dev_world(synthetic: Any) -> World:
    return build_world(synthetic, "dev")


def cfg(synthetic: Any, out: Path, **kw: Any) -> RunConfig:
    base: dict[str, Any] = {"suite": "smoke40", "snapshot": SNAP, "output": out, "root": synthetic.root, "db_url": synthetic.url,
                            "max_requests": 5000, "min_interval": 0.0}
    base.update(kw)
    return RunConfig(**base)


def read(out: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((out / "results.json").read_text(encoding="utf-8"))
    return data


def test_end_to_end_produces_complete_report(synthetic: Any, dev_world: World, tmp_path: Path) -> None:
    llm = ScriptedLLM(oracle_script(dev_world.resolved))
    results = run_eval(cfg(synthetic, tmp_path / "a", suite="futsito-v1", reps=2), lambda: llm)
    summary = results["summary"]
    g = summary["global"]
    assert g["evaluable"] > 60 and g["verdicts"]["not_evaluable"] > 0  # el sintético no cubre todas las capacidades: no evaluable aparte
    assert {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10"} <= set(summary["strata"])
    for s in summary["strata"].values():  # métricas por estrato
        assert {"accuracy", "critical", "tokens_by_source", "latency", "cost", "per_question", "abstention_correct"} <= set(s)
    assert g["accuracy"]["wilson95"][0] < g["accuracy"]["rate"] < g["accuracy"]["wilson95"][1] or g["accuracy"]["rate"] == 1.0
    assert g["per_question"]["llm_calls"] > 0 and g["per_question"]["mcp_calls"] > 0
    assert g["tokens_by_source"]["provider"]["input"] > 0 and g["cost"]["usd_per_1000"] is None  # sin tarifa: null, no inventado
    assert g["latency"]["llm_call_ms"]["p50"] is not None and g["latency"]["tool_call_ms"]["p95"] is not None
    assert summary["consistency"]["turns_with_repetitions"] > 0 and summary["consistency"]["same_verdict"]["rate"] == 1.0
    meta = results["meta"]
    assert meta["model"] == {"provider": "scripted", "model": "scripted-1"} and meta["snapshot"]["synthetic"] is True
    assert meta["commit"] and meta["clock"]["dependencies"] and meta["agent_flags"]["AGENT_TELEMETRY"] == "off"
    report = (tmp_path / "a" / "report.md").read_text(encoding="utf-8")
    for needle in ("SINTÉTICO", "Wilson", "Errores críticos", "Consistencia", "p50", "Tokens por origen", "Reloj de pared", "No evaluables"):
        assert needle in report
    # el oráculo responde con los hechos esperados: lo evaluable sale correcto (salvo las reglas que el KB sintético no contiene)
    graded = {(r["case_id"], r["turn"]): r for r in results["records"] if r["rep"] == 0 and r["verdict"] in ("correct", "partial", "fail", "critical")}
    bad = {k: (v["verdict"], v["reason"][:100]) for k, v in graded.items() if v["verdict"] != "correct" and k[0] not in ("s9_regla_roja_mano", "s9_regla_sexta_falta")}
    assert not bad, bad
    assert graded[("s9_regla_duracion_mitad", 0)]["verdict"] == "correct"  # reglamento vía search_rules sobre el KB sintético
    assert graded[("s1_puntos", 0)]["tools_used"] == ["get_standings"] and graded[("s1_puntos", 0)]["metrics"]["llm_calls"] == 2


def test_same_snapshot_and_script_give_identical_results(synthetic: Any, dev_world: World, tmp_path: Path) -> None:
    script = oracle_script(dev_world.resolved)
    run_eval(cfg(synthetic, tmp_path / "x", reps=2), lambda: ScriptedLLM(script))
    run_eval(cfg(synthetic, tmp_path / "y", reps=2), lambda: ScriptedLLM(script))
    a, b = read(tmp_path / "x"), read(tmp_path / "y")
    assert json.dumps(normalize(a), sort_keys=True) == json.dumps(normalize(b), sort_keys=True)
    assert a["records"] and a["summary"]["global"]["evaluable"] > 0


def test_repetitions_use_rotated_order() -> None:
    ids = list("ABCDEF")
    assert rotated(ids, 0, 3) == ids and rotated(ids, 1, 3) == list("CDEFAB") and rotated(ids, 2, 3) == list("EFABCD")
    assert sorted(rotated(ids, 2, 3)) == ids


def test_default_split_is_dev_and_holdout_needs_explicit_split(synthetic: Any, dev_world: World, tmp_path: Path) -> None:
    script = oracle_script(dev_world.resolved)
    default = run_eval(cfg(synthetic, tmp_path / "d", suite="futsito-v1"), lambda: ScriptedLLM(script))
    assert default["meta"]["split"] == "dev" and not any(r["holdout"] for r in default["records"])
    holdout_ids = {c.id for c in load_cases(synthetic.root, "holdout")}
    assert holdout_ids and holdout_ids.isdisjoint({r["case_id"] for r in default["records"]})
    hold = run_eval(cfg(synthetic, tmp_path / "h", suite="futsito-v1", split="holdout"),
                    lambda: ScriptedLLM(oracle_script(build_world(synthetic, "holdout").resolved)))
    assert hold["meta"]["split"] == "holdout" and {r["case_id"] for r in hold["records"]} == holdout_ids
    with pytest.raises(ValueError):
        suite_cases("smoke40", "holdout", synthetic.root)  # el smoke es un subconjunto de dev


def test_tampered_holdout_is_refused_but_dev_still_loads(tmp_path: Path) -> None:
    import shutil
    repo = Path(__file__).parents[2] / "evals" / "datasets" / "futsito-v1"
    dst = tmp_path / "evals" / "datasets" / "futsito-v1"
    shutil.copytree(repo, dst)
    target = dst / "s1_sencillas.holdout.yaml"
    target.write_text(target.read_text(encoding="utf-8").replace("¿Cuántos", "¿Cuantos", 1), encoding="utf-8")
    assert load_cases(tmp_path, "dev")  # el desarrollo no abre el holdout
    with pytest.raises(SealError):
        load_cases(tmp_path, "holdout")


def test_hard_request_cap_and_clean_stop_then_fill_missing(synthetic: Any, dev_world: World, tmp_path: Path) -> None:
    script = oracle_script(dev_world.resolved)
    full = run_eval(cfg(synthetic, tmp_path / "full"), lambda: ScriptedLLM(script))
    total_calls = sum(r["metrics"]["llm_calls"] for r in full["records"])
    assert total_calls > 30
    # 1) tope duro: el LLM nunca recibe más peticiones que --max-requests, y lo pendiente queda not_run
    capped_llm = ScriptedLLM(script)
    capped = run_eval(cfg(synthetic, tmp_path / "cap", max_requests=24), lambda: capped_llm)
    assert capped_llm.calls <= 24 and capped["meta"]["budget"]["used"] <= 24
    pending = [r for r in capped["records"] if r["verdict"] == "not_run"]
    assert pending and capped["meta"]["stopped"] == "presupuesto de peticiones agotado"
    # 2) parada por cuota del proveedor: el resto queda not_run, sin más peticiones
    quota_llm = ScriptedLLM(script, fail_after=20, fail_with=LLMQuotaError(f"{QUOTA_MARKER}: espera de horas", retry_after=7200))
    quota = run_eval(cfg(synthetic, tmp_path / "quota"), lambda: quota_llm)
    assert quota["meta"]["stopped"] == "cuota del proveedor agotada" and quota_llm.calls == 20
    assert sum(r["verdict"] == "not_run" for r in quota["records"]) > 0
    with pytest.raises(ValueError, match="no se mezclan ejecuciones"):  # otro brazo: no se mezclan resultados
        run_eval(cfg(synthetic, tmp_path / "quota", fill_missing=True, arm="compact"), lambda: ScriptedLLM(script))
    # 3) --fill-missing completa lo pendiente y el resultado coincide con la ejecución completa
    resumed = ScriptedLLM(script)
    done = run_eval(cfg(synthetic, tmp_path / "quota", fill_missing=True), lambda: resumed)
    assert not [r for r in done["records"] if r["verdict"] == "not_run"] and done["meta"]["stopped"] is None
    assert 0 < resumed.calls < total_calls  # solo se ejecutó lo pendiente
    key = lambda r: (r["rep"], r["case_id"], r["turn"], r["verdict"], r["reason"], r["answer"])
    assert [key(r) for r in done["records"]] == [key(r) for r in full["records"]]


def test_wrong_fingerprint_refuses_to_run(synthetic: Any, dev_world: World, tmp_path: Path) -> None:
    restored = snaps.restore_snapshot(SNAP, synthetic.admin_url, synthetic.root)
    assert restored.ok, restored.detail
    from sqlalchemy import create_engine, text
    engine = create_engine(restored.url)
    with engine.begin() as c:
        c.execute(text("UPDATE matches SET home_score = home_score + 1 WHERE id = (SELECT min(id) FROM matches WHERE home_score IS NOT NULL)"))
    engine.dispose()
    llm = ScriptedLLM(oracle_script(dev_world.resolved))
    with pytest.raises(FingerprintMismatch, match="NO es el snapshot"):
        run_eval(cfg(synthetic, tmp_path / "bad", db_url=restored.url), lambda: llm)
    assert llm.calls == 0 and not (tmp_path / "bad" / "results.json").exists()
    # sin --db el runner restaura el volcado en la BD desechable y la huella coincide
    good = run_eval(cfg(synthetic, tmp_path / "ok", db_url=None, admin_url=synthetic.admin_url, suite="smoke40"),
                    lambda: ScriptedLLM(oracle_script(dev_world.resolved)))
    assert good["meta"]["snapshot"]["fingerprint"] == synthetic.manifest["fingerprint"]["sha256"]


def test_snapshot_manifest_and_restore(synthetic: Any) -> None:
    m = synthetic.manifest
    assert m["id"] == SNAP and m["synthetic"] is True and m["as_of"].startswith("2026-10-20T12:00:00+02:00")  # Europe/Madrid (CEST)
    assert len(m["dump_sha256"]) == 64 and m["alembic_revision"] == _alembic_head() and m["kb_documents"] == ["fifa-futsal-laws-2025-26"]
    assert m["counts"] == {"seasons": 2, "competitions": 3, "groups": 4, "teams": 57, "matches": 63, "reports": 5}
    assert len(m["fingerprint"]["sha256"]) == 64
    ok, detail = snaps.verify_db(m, synthetic.url)
    assert ok, detail
    assert snaps.db_name(SNAP) == "futsal_eval_synthetic_dev"
    with pytest.raises(snaps.SnapshotError):
        snaps.load_manifest("no-existe", synthetic.root)


def test_report_uses_the_single_price_source(synthetic: Any, dev_world: World, tmp_path: Path) -> None:
    class Priced(ScriptedLLM):
        provider, model = "gemini", "gemini-3.5-flash-lite"

    results = run_eval(cfg(synthetic, tmp_path / "p"), lambda: Priced(oracle_script(dev_world.resolved)))
    g = results["summary"]["global"]
    assert g["cost"]["priced_questions"] == g["cost"]["questions"] > 0 and g["cost"]["usd_per_1000"] > 0
    assert results["meta"]["prices"]["source"].startswith("https://") and results["meta"]["prices"]["verified_at"]
    report = (tmp_path / "p" / "report.md").read_text(encoding="utf-8")
    assert "Tarifa usada (gemini:gemini-3.5-flash-lite" in report and "verified_at" in report



def _alembic_head() -> str:
    """Cabeza de alembic leída de los ficheros (importar las migraciones fuera de contexto falla: 0003 usa `op.f`)."""
    import re

    revs: dict[str, str | None] = {}
    for f in (Path(__file__).parents[2] / "alembic" / "versions").glob("*.py"):
        src = f.read_text(encoding="utf-8")
        rev = re.search(r"^revision\s*(?::[^=]+)?=\s*['\"]([^'\"]+)", src, re.MULTILINE)
        down = re.search(r"^down_revision\s*(?::[^=]+)?=\s*['\"]([^'\"]+)", src, re.MULTILINE)
        if rev:
            revs[rev.group(1)] = down.group(1) if down else None
    heads = set(revs) - {d for d in revs.values() if d}
    assert len(heads) == 1, heads
    return heads.pop()
