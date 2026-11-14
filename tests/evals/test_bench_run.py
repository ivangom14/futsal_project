"""Benchmark de extremo a extremo sobre el snapshot sintético, SIN red ni LLM real: dos «modelos» de guion fijo (uno responde con la
verdad, otro peor) a través del agente completo y del MCP real, con presupuesto, reanudación, fallo aislado, informe y evaluación ciega."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from world import ScriptedLLM, build_world, oracle_script

from futsal.agent.llm import LLMClient, LLMError
from futsal.evals.bench import cli
from futsal.evals.bench.blind import build_blind, reveal
from futsal.evals.bench.registry import ModelSpec, parse_model
from futsal.evals.bench.report import build_report
from futsal.evals.bench.runner import BenchConfig, BenchError, run_bench

ROOT = Path(__file__).parents[2]
SNAP = "synthetic-dev"


@pytest.fixture(scope="module")
def root(synthetic: Any) -> Path:
    bench = synthetic.root / "evals" / "bench"
    if not bench.exists():
        bench.symlink_to(ROOT / "evals" / "bench")
    return Path(synthetic.root)


@pytest.fixture(scope="module")
def script(synthetic: Any) -> dict[Any, Any]:
    return oracle_script(build_world(synthetic, "dev").resolved)


def model(mid: str, price_in: float = 1.0, price_out: float = 4.0) -> ModelSpec:
    return parse_model({"id": mid, "provider": "anthropic", "adapter": "anthropic", "model": f"sim-{mid}", "credentials_env": f"KEY_{mid.upper()}",
                        "pricing": {"input": price_in, "output": price_out, "cached_input": 0.1, "source": "prueba", "checked_at": "2026-11-14"},
                        "limits": {"min_interval_s": 0.0, "max_requests": 2000}})


def degraded(script: dict[Any, Any]) -> dict[Any, Any]:
    """Peor modelo: sin herramientas en la mitad de las preguntas (se abstiene donde había datos) y con un marcador inventado en otras."""
    out: dict[Any, Any] = {}
    for i, (key, (steps, text)) in enumerate(sorted(script.items())):
        out[key] = ([], "No dispongo de esa información.") if i % 2 else (steps, text + " Ganaron 9-9 el último partido.") if i % 5 == 0 else (steps, text)
    return out


def cfg(root: Path, synthetic: Any, tmp: Path, models: list[ModelSpec], **kw: Any) -> BenchConfig:
    base: dict[str, Any] = {"models": models, "snapshot": SNAP, "root": root, "db_url": synthetic.url, "output_root": tmp, "budget_usd": 5.0}
    base.update(kw)
    return BenchConfig(**base)


def factory(script: dict[Any, Any], fail: set[str] | None = None) -> Any:
    def build(spec: ModelSpec) -> LLMClient:
        if fail and spec.id in fail:
            raise LLMError(f"proveedor caído para {spec.id} con clave {os.environ.get(spec.credentials_env)}")
        return ScriptedLLM(script if spec.id == "bueno" else degraded(script))
    return build


@pytest.fixture()
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("KEY_BUENO", "KEY_MALO", "KEY_ROTO"):
        monkeypatch.setenv(k, f"sk-prueba-{k.lower()}-secreta")


def test_full_comparison_report_and_blind_evaluation(root: Path, synthetic: Any, script: dict[Any, Any], tmp_path: Path, keys: None) -> None:
    meta = run_bench(cfg(root, synthetic, tmp_path, [model("bueno"), model("malo", 0.1, 0.4)], run_id="r1"), factory(script))
    run = tmp_path / "r1"
    assert meta["models_status"] == {"bueno": {"status": "complete", "detail": None, "requests": meta["models_status"]["bueno"]["requests"]},
                                     "malo": {"status": "complete", "detail": None, "requests": meta["models_status"]["malo"]["requests"]}}
    assert sorted(meta["model_order"]) == ["bueno", "malo"] and meta["seed"] and len(meta["case_ids"]) == 39
    assert meta["models"]["bueno"]["pricing"]["source"] == "prueba" and "sk-prueba" not in (run / "bench.json").read_text(encoding="utf-8")
    assert meta["budget"]["spent_usd_calculated"] > 0
    a = build_report(run)
    sb, sm = a["stats"]["bueno"], a["stats"]["malo"]
    assert sb["quality"]["accuracy"]["rate"] > sm["quality"]["accuracy"]["rate"]
    assert sm["quality"]["classes"]["alucinacion"] > sb["quality"]["classes"]["alucinacion"]
    assert a["comparison"]["rank_quality"][0] == "bueno" and a["comparison"]["recommendations"]["best_overall"]["model"] == "bueno"
    # coste por llamada calculado con la tarifa del registro y los tokens del proveedor (simulados)
    row = next(r for r in a["rows"]["bueno"] if r["llm_calls"] >= 2)
    assert row["cost_basis"] == "provider" and row["cost_usd"] == pytest.approx(sum(c["cost_usd"] for c in row["calls"]))
    assert sb["cost"]["per_question"]["mean"] and sb["cost"]["projections"]["10000"]["usd"] and sb["cost"]["per_correct"]
    assert sb["latency"]["p50_ms"] is not None and sb["usage"]["mcp_calls_per_question"] > 0
    assert sb["cost"]["per_question"]["mean"] > sm["cost"]["per_question"]["mean"]  # más caro por tarifa
    report = (run / "report.md").read_text(encoding="utf-8")
    for needle in ("A. Ranking de calidad", "B. Ranking económico", "C. Ranking de velocidad", "D. Calidad / precio", "Pareto",
                   "Por categoría", "Router híbrido", "Fallos relevantes", "SINTÉTICO", "no es lo facturado"):
        assert needle in report
    assert json.loads((run / "summary.json").read_text(encoding="utf-8"))["stats"]["bueno"]["model"]["model"] == "sim-bueno"

    info = build_blind(run, seed=3)
    blind = run / "blind"
    items_md = (blind / "items.md").read_text(encoding="utf-8")
    assert info["items"] > 30 and "bueno" not in items_md and "malo" not in items_md and "sim-" not in items_md
    assert "Evidencia" in items_md and "coste" not in items_md.lower()
    key = json.loads((blind / "key.json").read_text(encoding="utf-8"))["key"]
    assert len({tuple(v.values()) for v in key.values()}) == 2  # la letra de cada modelo cambia entre conversaciones
    assert build_blind(run, seed=3) and json.loads((blind / "key.json").read_text(encoding="utf-8"))["key"] == key  # semilla reproducible
    prompt = (blind / "PROMPT.md").read_text(encoding="utf-8")
    assert "No premies la extensión" in prompt and "key.json" in prompt and "reveal" in prompt
    # el «juez» rellena la hoja: copia la clase determinista (simulación) y revela
    sheet = json.loads((blind / "scoresheet.json").read_text(encoding="utf-8"))
    det = {m: {(r["case_id"], r["turn"]): r["class"] for r in a["rows"][m] if r["rep"] == 0} for m in ("bueno", "malo")}
    item_case = {f"I{n:02d}": cid for n, cid in enumerate(meta["case_ids"], 1)}
    for entry in sheet["items"]:
        for letter, val in entry["labels"].items():
            m = key[entry["item"]][letter]
            classes = [c for (cid, _t), c in det[m].items() if cid == item_case[entry["item"]]]
            val["class"] = max(classes, key=["correcta", "parcial", "incorrecta", "alucinacion", "error_tecnico", "no_evaluable", "no_ejecutada"].index)
            val["scores"] = {c: 2 for c in val["scores"]}
    (blind / "scoresheet.json").write_text(json.dumps(sheet), encoding="utf-8")
    res = reveal(run)
    assert set(res["models"]) == {"bueno", "malo"} and res["unscored_labels"] == 0
    for s in res["models"].values():
        ag = s["agreement_with_deterministic"]
        assert ag["n"] > 0 and ag["k"] == ag["n"] and s["cost"]["mean"] is not None
    assert "Revelado" in (blind / "reveal.md").read_text(encoding="utf-8")


def test_budget_stops_cleanly_and_resume_completes_without_repeating(root: Path, synthetic: Any, script: dict[Any, Any], tmp_path: Path,
                                                                     keys: None) -> None:
    specs = [model("bueno")]
    first = run_bench(cfg(root, synthetic, tmp_path, specs, run_id="b", budget_usd=0.03, limit=10), factory(script))
    st = first["models_status"]["bueno"]
    assert st["status"] == "stopped" and "presupuesto" in st["detail"] and first["budget"]["refused_calls"] >= 1
    assert first["budget"]["spent_usd_calculated"] <= 0.03
    done = json.loads((tmp_path / "b" / "bueno" / "results.json").read_text(encoding="utf-8"))
    n_done = sum(r["verdict"] != "not_run" for r in done["records"])
    assert 0 < n_done < len(done["records"])
    second = run_bench(cfg(root, synthetic, tmp_path, specs, run_id="b", budget_usd=5.0, limit=10, resume=True), factory(script))
    assert second["models_status"]["bueno"]["status"] == "complete"
    final = json.loads((tmp_path / "b" / "bueno" / "results.json").read_text(encoding="utf-8"))
    assert not any(r["verdict"] == "not_run" for r in final["records"])
    with pytest.raises(BenchError, match="no coinciden"):
        run_bench(cfg(root, synthetic, tmp_path, specs, run_id="b", limit=5, resume=True), factory(script))


def test_failing_model_does_not_invalidate_the_others_and_secrets_are_redacted(root: Path, synthetic: Any, script: dict[Any, Any],
                                                                               tmp_path: Path, keys: None) -> None:
    meta = run_bench(cfg(root, synthetic, tmp_path, [model("bueno"), model("roto")], run_id="f", limit=4), factory(script, {"roto"}))
    assert meta["models_status"]["bueno"]["status"] == "complete"
    roto = meta["models_status"]["roto"]
    assert roto["status"] == "failed" and "sk-prueba" not in roto["detail"] and "***" in roto["detail"]
    a = build_report(tmp_path / "f")
    assert set(a["stats"]) == {"bueno"} and any("roto" in w for w in a["warnings"])


def test_dry_run_needs_no_keys_and_live_run_refuses_without_budget_or_keys(root: Path, synthetic: Any, tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("KEY_BUENO", "KEY_MALO"):
        monkeypatch.delenv(k, raising=False)
    meta = run_bench(cfg(root, synthetic, tmp_path, [model("bueno"), model("malo")], run_id="d", dry_run=True, budget_usd=None, limit=3))
    assert meta["kind"] == "dry_run" and all(s["status"] == "complete" for s in meta["models_status"].values())
    assert meta["budget"]["spent_usd_calculated"] == 0
    a = build_report(tmp_path / "d")
    assert a["warnings"][0].startswith("snapshot SINTÉTICO") and any("dry-run" in w for w in a["warnings"])
    with pytest.raises(BenchError, match="--budget-usd"):
        run_bench(cfg(root, synthetic, tmp_path, [model("bueno")], run_id="x", budget_usd=None))
    with pytest.raises(BenchError, match="faltan credenciales"):
        run_bench(cfg(root, synthetic, tmp_path, [model("bueno")], run_id="y"))
    with pytest.raises(BenchError, match="ya existe"):
        run_bench(cfg(root, synthetic, tmp_path, [model("bueno")], run_id="d", dry_run=True, budget_usd=None))


def test_cli_requires_explicit_live_confirmation_and_lists_without_secrets(root: Path, capsys: pytest.CaptureFixture[str],
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit):
        cli.main(["run", "--snapshot", SNAP])  # sin --dry-run ni --confirm-live: argparse lo rechaza
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-nunca-visible")
    assert cli.main(["models"]) == 0
    out = capsys.readouterr().out
    assert "claude-haiku" in out and "sk-ant-nunca-visible" not in out and "definida" in out
    assert cli.main(["plan", "--limit", "5"]) == 0
    assert "SIN llamadas" in capsys.readouterr().out


def test_v2_agent_is_the_default_and_its_tools_query_the_snapshot_not_database_url(root: Path, synthetic: Any, script: dict[Any, Any],
                                                                                   tmp_path: Path, keys: None,
                                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    world = build_world(synthetic, "dev")
    q = world.rc("s10_fecha_fin_de_semana").turns[0].user
    v2_script = dict(script)
    v2_script[(q, q)] = ([("matches_by_date", {"date_from": "2026-10-24", "date_to": "2026-10-25"})], "Hay partidos el fin de semana.")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nadie:x@127.0.0.1:1/no_existe")  # la BD habitual no debe tocarse
    meta = run_bench(cfg(root, synthetic, tmp_path, [model("bueno")], run_id="v2", context_mode="legacy"),
                     lambda spec: ScriptedLLM(v2_script))
    assert meta["agent"] == {"arch": "v2", "context_mode": "legacy"} and meta["models_status"]["bueno"]["status"] == "complete"
    res = json.loads((tmp_path / "v2" / "bueno" / "results.json").read_text(encoding="utf-8"))
    assert res["meta"]["agent"] == {"arch": "v2", "context_mode": "legacy"}
    rec = next(r for r in res["records"] if r["case_id"] == "s10_fecha_fin_de_semana")
    assert "matches_by_date" in rec["tools_used"] and rec["metrics"]["errors"] == 0  # consultó el snapshot sin error
    exp = res["cases"]["s10_fecha_fin_de_semana"]["expected"][0]
    if exp.get("v2"):
        assert exp["behavior"] == exp["v2"]["behavior"]  # calificado (y mostrado al juez) con la expectativa v2
    report = build_report(tmp_path / "v2")
    assert report["meta"]["agent"]["arch"] == "v2" and "agente **v2**" in (tmp_path / "v2" / "report.md").read_text(encoding="utf-8")
    with pytest.raises(BenchError, match="agente"):
        run_bench(cfg(root, synthetic, tmp_path, [model("bueno")], run_id="v2", agent_arch="legacy", resume=True),
                  lambda spec: ScriptedLLM(v2_script))


def test_live_mode_without_snapshot_reads_the_current_db_and_warns_if_data_change(root: Path, synthetic: Any, script: dict[Any, Any],
                                                                                tmp_path: Path, keys: None) -> None:
    from sqlalchemy import text

    from futsal.evals.runner import LIVE

    # 1) sin cambios durante la ejecución: misma huella al empezar y al terminar
    meta = run_bench(cfg(root, synthetic, tmp_path, [model("bueno")], run_id="live1", snapshot=LIVE, limit=4,
                         as_of="2026-10-20T12:00"), factory(script))
    snap = meta["snapshot"]
    assert snap["live"] is True and snap["as_of"].startswith("2026-10-20T12:00") and snap["data_changed"] is False
    a = build_report(tmp_path / "live1")
    assert a["warnings"][0].startswith("datos vivos (sin snapshot): no cambiaron")
    assert "Datos vivos (sin snapshot)" in (tmp_path / "live1" / "report.md").read_text(encoding="utf-8")

    # 2) el «cron» escribe a mitad de la ejecución: se detecta y se avisa con las tablas afectadas
    def writer_factory(spec: ModelSpec) -> LLMClient:
        with synthetic.engine.begin() as c:
            c.execute(text("INSERT INTO ingest_controls (source, paused) VALUES ('prueba-live', false)"))
        return ScriptedLLM(script)

    try:
        meta2 = run_bench(cfg(root, synthetic, tmp_path, [model("bueno")], run_id="live2", snapshot=LIVE, limit=2,
                              as_of="2026-10-20T12:00"), writer_factory)
        assert meta2["snapshot"]["data_changed"] is True and meta2["snapshot"]["changed_tables"] == ["ingest_controls"]
        assert "CAMBIARON durante la ejecución" in build_report(tmp_path / "live2")["warnings"][0]
    finally:
        with synthetic.engine.begin() as c:
            c.execute(text("DELETE FROM ingest_controls WHERE source = 'prueba-live'"))
