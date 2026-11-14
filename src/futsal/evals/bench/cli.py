"""CLI del benchmark multimodelo: `python -m futsal.evals.bench {models,plan,run,report,regrade,blind,reveal,history}`.

`models`, `plan`, `report`, `blind`, `reveal` y `history` no llaman a ningún LLM. `run` solo llama a modelos reales con
`--confirm-live` y `--budget-usd`; `run --dry-run` usa un modelo simulado (sin red ni gasto) para validar todo el circuito.
Salida: 0 correcto · 1 error de uso o configuración · 3 la BD no es el snapshot · 4 alguna ejecución quedó parada o fallida
(presupuesto, cuota o error: completa con `--resume`).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from futsal.config.settings import database_url
from futsal.evals.bench import registry
from futsal.evals.bench.runner import (
    BENCH_FILE,
    DEFAULT_ROOT,
    DEFAULT_SEED,
    BenchConfig,
    BenchError,
    load_suite,
    run_bench,
    suite_case_ids,
)
from futsal.evals.runner import LIVE
from futsal.evals.snapshot import SnapshotError

EXIT_FINGERPRINT, EXIT_INCOMPLETE = 3, 4
OUTPUT_PESSIMISM = 5  # en el plan: salida × 5 como escenario pesimista (modelos que razonan facturan el razonamiento como salida)


def _models_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--models", default=None, help="ids separados por comas (por defecto, los enabled del registro)")
    p.add_argument("--registry", type=Path, default=None, help="fichero de modelos (por defecto evals/bench/models.yaml)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m futsal.evals.bench", description="Benchmark multimodelo de Futsito")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("models", help="lista los modelos configurados (sin mostrar claves)")
    m.add_argument("--registry", type=Path, default=None)
    m.add_argument("--json", action="store_true")
    for name, help_ in (("plan", "estima peticiones y coste SIN llamar a nada"), ("run", "ejecuta la comparación")):
        p = sub.add_parser(name, help=help_)
        _models_arg(p)
        p.add_argument("--suite", default="bench-v1")
        p.add_argument("--limit", type=int, default=None, help="solo los N primeros casos (intercalados por categoría)")
        p.add_argument("--reps", type=int, default=1)
        p.add_argument("--budget-usd", type=float, default=None, help="tope de gasto calculado en USD para TODA la ejecución")
        if name == "plan":
            p.add_argument("--calibrate", type=Path, default=None, help="directorio de una ejecución anterior: usa el consumo medido por modelo")
        else:
            src = p.add_mutually_exclusive_group(required=True)
            src.add_argument("--snapshot", help="snapshot congelado (recomendado: mismos datos para todos y comparable en el tiempo)")
            src.add_argument("--live", action="store_true",
                             help="sin snapshot: la BD actual en solo lectura; el informe avisa si los datos cambian durante la ejecución")
            p.add_argument("--as-of", default=None, help="solo con --live: «hoy» de la prueba (ISO; sin zona = Europe/Madrid)")
            p.add_argument("--db", default=None, help="BD del snapshot ya restaurada (si no, se restaura en futsal_eval_<id>)")
            p.add_argument("--admin-db", default=None)
            p.add_argument("--run-id", default=None)
            p.add_argument("--resume", action="store_true", help="completa lo pendiente de --run-id sin repetir lo hecho")
            p.add_argument("--seed", type=int, default=DEFAULT_SEED)
            p.add_argument("--agent", choices=("v2", "legacy"), default="v2", help="arquitectura del agente (por defecto v2, la del frontal)")
            p.add_argument("--context", choices=("legacy", "deterministic", "llm"), default=None,
                           help="comprensión conversacional; por defecto, AGENT_CONTEXT del entorno (el mismo .env que el frontal)")
            p.add_argument("--arm", default="legacy", help="banderas del agente (agent/arms.py); las mismas para todos los modelos")
            p.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
            g = p.add_mutually_exclusive_group(required=True)
            g.add_argument("--dry-run", action="store_true", help="modelo simulado: sin red ni gasto")
            g.add_argument("--confirm-live", action="store_true", help="imprescindible para llamar a los modelos reales (de pago)")
    r = sub.add_parser("report", help="regenera summary.json y report.md de una ejecución")
    r.add_argument("run_dir", type=Path)
    r.add_argument("--gap", type=float, default=0.05, help="diferencia de exactitud tolerada como «comparable» (0.05 = 5 puntos)")
    rg = sub.add_parser("regrade", help="recalifica una ejecución con el calificador actual y la evidencia guardada (SIN llamar al LLM)")
    rg.add_argument("run_dir", type=Path)
    rg.add_argument("--db", default=None, help="BD del snapshot ya restaurada (si no, se usa DATABASE_URL para restaurarlo o leer la BD viva)")
    rg.add_argument("--output", type=Path, default=None, help="directorio nuevo (por defecto <run>-regraded)")
    b = sub.add_parser("blind", help="prepara la evaluación ciega para Opus")
    b.add_argument("run_dir", type=Path)
    b.add_argument("--seed", type=int, default=7)
    b.add_argument("--rep", type=int, default=1, help="repetición que se juzga (1 = la primera)")
    v = sub.add_parser("reveal", help="une la hoja del juez con la clave y añade costes y tiempos")
    v.add_argument("run_dir", type=Path)
    h = sub.add_parser("history", help="ejecuciones guardadas y sus cifras principales")
    h.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    return ap


def _ids(raw: str | None) -> list[str] | None:
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else None


def cmd_models(a: argparse.Namespace) -> int:
    specs = registry.load_registry(a.registry)
    if a.json:
        print(json.dumps({k: {**s.as_dict(), "credentials_present": s.has_credentials()} for k, s in specs.items()}, ensure_ascii=False, indent=1))
        return 0
    for s in specs.values():
        p = s.pricing
        print(f"{'✓' if s.enabled else '·'} {s.id:<20} {s.provider}/{s.model:<24} adaptador={s.adapter:<9} "
              f"credencial {s.credentials_env}={'definida' if s.has_credentials() else 'NO definida'} · tarifa {p.status} "
              f"(in {p.input} / caché {p.cached_input} / out {p.output} USD/1M · {p.checked_at or 'sin fecha'})")
    print("✓ = activado para el benchmark · las claves nunca se muestran")
    return 0


def plan_rows(specs: list[registry.ModelSpec], turns: int, reps: int, calibrate: Path | None) -> list[dict[str, Any]]:
    from futsal.evals.plan import RETRY_MARGIN, per_question

    rows = []
    for s in specs:
        cal = calibrate / s.id / "results.json" if calibrate else None
        try:
            pq = per_question(cal if cal and cal.exists() else None)
        except ValueError:
            pq = per_question(None)
        questions = turns * reps
        p = s.pricing

        def usd(out_factor: float, _pq: dict[str, Any] = pq, _p: registry.Pricing = p, _n: int = questions) -> float | None:
            if _p.input is None or _p.output is None:
                return None
            return float(_n * (float(_pq["input_tokens"]) * _p.input + float(_pq["output_tokens"]) * out_factor * _p.output) / 1_000_000)

        req = round(questions * pq["llm_calls"] * RETRY_MARGIN)
        rows.append({"model": s.id, "questions": questions, "per_question": pq, "requests_estimated": req,
                     "requests_cap": s.max_requests, "fits_cap": req <= s.max_requests, "usd_estimated": usd(1.0),
                     "usd_pessimistic": usd(OUTPUT_PESSIMISM), "credentials": s.has_credentials(), "pricing_status": p.status,
                     "minutes_min": round(req * s.min_interval_s / 60, 1)})
    return rows


def cmd_plan(a: argparse.Namespace) -> int:
    specs = registry.select(registry.load_registry(a.registry), _ids(a.models))
    suite = load_suite(a.suite)
    ids = suite_case_ids(suite, a.limit)
    turns = sum(c["turns"] for c in suite["cases"] if c["id"] in set(ids))
    rows = plan_rows(specs, turns, a.reps, a.calibrate)
    print(f"plan · suite {a.suite} v{suite.get('version')} · {len(ids)} casos · {turns} turnos × {a.reps} rep. · SIN llamadas")
    total = 0.0
    for r in rows:
        pq = r["per_question"]
        est, pes = r["usd_estimated"], r["usd_pessimistic"]
        total += pes or 0.0
        print(f"- {r['model']}: {r['questions']} preguntas · ≈{r['requests_estimated']} peticiones (tope {r['requests_cap']}"
              f"{'' if r['fits_cap'] else ' ¡INSUFICIENTE!'}) · ≥{r['minutes_min']} min por ritmo · coste ≈{'n/d' if est is None else f'{est:.3f}'} $ "
              f"(pesimista {'n/d' if pes is None else f'{pes:.3f}'} $) · credencial {'sí' if r['credentials'] else 'NO'} · tarifa {r['pricing_status']}")
        print(f"    supuestos: {pq['source']}; {pq['llm_calls']} llamadas, {pq['input_tokens']:.0f} tokens de entrada y {pq['output_tokens']:.0f} de salida por pregunta"
              + (f"; {'; '.join(pq['assumed'])}" if pq["assumed"] else "") + f"; pesimista = salida × {OUTPUT_PESSIMISM}")
    print(f"Presupuesto sugerido: --budget-usd {max(round(total * 1.2, 2), 0.05)} (pesimista total + 20 %). "
          "Son ESTIMACIONES; el coste real se mide en la ejecución.")
    if a.budget_usd is not None and total > a.budget_usd:
        print(f"Aviso: el escenario pesimista ({total:.3f} $) supera --budget-usd {a.budget_usd}: la ejecución podría quedar a medias (se completa con --resume).")
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    from futsal.evals.runner import FingerprintMismatch

    specs = registry.select(registry.load_registry(a.registry), _ids(a.models))
    if a.as_of and not a.live:
        print("error: --as-of solo se usa con --live (el snapshot ya fija su fecha)", file=sys.stderr)
        return 1
    cfg = BenchConfig(models=specs, snapshot=LIVE if a.live else a.snapshot, as_of=a.as_of, suite=a.suite, reps=a.reps, limit=a.limit, budget_usd=a.budget_usd,
                      output_root=a.output_root, run_id=a.run_id, resume=a.resume, seed=a.seed, arm=a.arm, agent_arch=a.agent, context_mode=a.context, db_url=a.db,
                      admin_url=a.admin_db or (None if a.db else database_url()), dry_run=a.dry_run, progress=lambda m: print(m, flush=True))
    try:
        meta = run_bench(cfg)
    except FingerprintMismatch as exc:
        print(f"NO SE EJECUTA: {exc}", file=sys.stderr)
        return EXIT_FINGERPRINT
    from futsal.evals.bench.report import build_report
    run_dir = a.output_root / meta["run_id"]
    build_report(run_dir)
    print(f"resultados en {run_dir} (bench.json, <modelo>/results.json, summary.json, report.md) · gasto calculado "
          f"{meta['budget']['spent_usd_calculated']:.4f} $")
    bad = {k: v for k, v in meta["models_status"].items() if v["status"] != "complete"}
    for k, v in bad.items():
        print(f"  {k}: {v['status']} — {v['detail']}")
    return EXIT_INCOMPLETE if bad else 0


def cmd_report(a: argparse.Namespace) -> int:
    from futsal.evals.bench.report import build_report
    an = build_report(a.run_dir, a.gap)
    print(f"summary.json y report.md en {a.run_dir} · avisos: {len(an['warnings'])}")
    return 0


def cmd_regrade(a: argparse.Namespace) -> int:
    from futsal.evals.bench.regrade import regrade_run
    from futsal.evals.bench.report import build_report
    res = regrade_run(a.run_dir, a.db, None if a.db else database_url(), output=a.output)
    out = Path(res["output"])
    build_report(out)
    print(f"recalificado en {out} (0 llamadas al LLM):")
    for mid, m in res["models"].items():
        acc_before, acc_after, n = m["accuracy"]
        print(f"  {mid}: {m['changed']} veredictos cambian · exactitud {acc_before}/{n} → {acc_after}/{n} · críticos "
              f"{m['critical'][0]} → {m['critical'][1]}" + (f" · {m['without_evidence']} turnos sin evidencia (sin recalificar)"
                                                           if m["without_evidence"] else ""))
    return 0


def cmd_blind(a: argparse.Namespace) -> int:
    from futsal.evals.bench.blind import build_blind
    info = build_blind(a.run_dir, a.seed, a.rep - 1)
    print(f"evaluación ciega en {info['dir']}: {info['items']} conversaciones, letras {', '.join(info['labels'])}. "
          f"Pide a Opus que siga {info['dir']}/PROMPT.md (no abras key.json).")
    return 0


def cmd_reveal(a: argparse.Namespace) -> int:
    from futsal.evals.bench.blind import reveal
    res = reveal(a.run_dir)
    print(f"blind/reveal.md y reveal.json en {a.run_dir} · modelos {', '.join(sorted(res['models']))} · sin puntuar {res['unscored_labels']}")
    return 0


def cmd_history(a: argparse.Namespace) -> int:
    runs = sorted(p for p in a.output_root.glob(f"*/{BENCH_FILE}")) if a.output_root.exists() else []
    if not runs:
        print(f"sin ejecuciones en {a.output_root}")
        return 0
    for f in runs:
        meta = json.loads(f.read_text(encoding="utf-8"))
        summ = f.parent / "summary.json"
        line = f"{meta['run_id']} · {meta['kind']} · suite {meta['suite']['name']} v{meta['suite']['version']} · snapshot {meta['snapshot']['id']}"
        if summ.exists():
            stats = json.loads(summ.read_text(encoding="utf-8"))["stats"]
            line += " · " + " · ".join(
                f"{m}: {((s['quality']['accuracy']['rate'] or 0) * 100):.0f} % / {s['cost']['per_question']['mean']} $ por pregunta" for m, s in stats.items())
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"models": cmd_models, "plan": cmd_plan, "run": cmd_run, "report": cmd_report, "regrade": cmd_regrade, "blind": cmd_blind,
                "reveal": cmd_reveal, "history": cmd_history}
    try:
        return handlers[args.cmd](args)
    except SnapshotError as exc:
        snap = getattr(args, "snapshot", "<id>")
        print(f"error: {exc}\nCrea antes el snapshot con tus datos actuales:\n"
              f"  python -m futsal.evals snapshot create --id {snap} --as-of AAAA-MM-DDTHH:MM", file=sys.stderr)
        return 1
    except (registry.RegistryError, BenchError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
