"""CLI: `python -m futsal.evals {plan,run,report,regrade,compare,snapshot}`.

Códigos de salida: 0 correcto · 1 error de uso o de datos · 2 (`compare`) sin diferencia demostrada · 3 la huella de la BD no coincide con
el snapshot (no se ejecuta nada) o (`snapshot import`) la BD de destino ya existe · 4 cuota del proveedor agotada o presupuesto de peticiones agotado (hay casos `not_run`; reanudar con
`--fill-missing`). `compare`: 0 B mejor · 1 B rechazado · 2 sin diferencia demostrada.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from futsal.agent.arms import ARMS, QUOTA_EXIT_CODE
from futsal.config.settings import database_url
from futsal.evals import compare as comparing
from futsal.evals import plan as planning
from futsal.evals import report as reporting
from futsal.evals import snapshot as snaps
from futsal.evals.datasets import SPLITS, SUITES, verify_seals
from futsal.evals.runner import FingerprintMismatch, RunConfig, run_eval

EXIT_FINGERPRINT = 3


def _add_suite_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--suite", choices=SUITES, default="smoke40")
    p.add_argument("--split", choices=SPLITS, default="dev", help="dev por defecto; el holdout exige --split holdout explícito")
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--root", type=Path, default=Path("."))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m futsal.evals", description="Futsito-Eval v1")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan", help="estima llamadas, tokens y coste SIN llamar a nada")
    _add_suite_args(sp)
    sp.add_argument("--calibrate", type=Path, help="results.json previo para calibrar el coste medio por pregunta")
    sp.add_argument("--provider")
    sp.add_argument("--model")
    sp.add_argument("--max-requests", type=int)
    sp.add_argument("--db", help="solo con regression: cuenta los casos en esa BD en lugar de usar las cifras declaradas")

    rp = sub.add_parser("run", help="ejecuta una suite contra el snapshot")
    _add_suite_args(rp)
    rp.add_argument("--snapshot", required=True)
    rp.add_argument("--db", help="BD ya restaurada (si no, se restaura el volcado en futsal_eval_<id>)")
    rp.add_argument("--admin-db", default=None, help="URL con la que restaurar (por defecto DATABASE_URL)")
    rp.add_argument("--max-requests", type=int, required=True, help="tope DURO de peticiones al LLM (llamadas + reintentos)")
    rp.add_argument("--min-interval", type=float, default=4.0)
    rp.add_argument("--output", type=Path, required=True)
    rp.add_argument("--fill-missing", action="store_true", help="completa solo lo que quedó not_run en --output")
    rp.add_argument("--arm", choices=sorted(ARMS), default="legacy")
    rp.add_argument("--confirm-live", action="store_true", help="imprescindible: la ejecución llama al LLM real (de pago)")

    pr = sub.add_parser("report", help="regenera report.md de un results.json")
    pr.add_argument("output", type=Path, help="directorio con results.json")

    rg = sub.add_parser("regrade", help="recalifica una ejecución de `regression` ya hecha, SIN llamar al LLM (salida en un directorio nuevo)")
    rg.add_argument("--run", type=Path, required=True, help="directorio con results.json (no se modifica)")
    rg.add_argument("--snapshot", required=True)
    rg.add_argument("--db", default=None, help="BD del snapshot: verifica la huella y permite recalcular la invención con la evidencia guardada")
    rg.add_argument("--output", type=Path, default=None, help="directorio NUEVO (por defecto <run>-regraded)")
    rg.add_argument("--root", type=Path, default=Path("."))

    cp = sub.add_parser("compare", help="compara A (referencia) con B (candidata)")
    cp.add_argument("a", type=Path)
    cp.add_argument("b", type=Path)
    cp.add_argument("--noise", type=Path, help="segunda ejecución de la referencia: fija el umbral de ruido por estrato")

    sn = sub.add_parser("snapshot", help="crea, restaura o verifica un snapshot")
    ssub = sn.add_subparsers(dest="action", required=True)
    cr = ssub.add_parser("create")
    cr.add_argument("--id", required=True)
    cr.add_argument("--as-of", required=True, help="ISO; sin zona = Europe/Madrid")
    cr.add_argument("--db", default=None)
    cr.add_argument("--synthetic", action="store_true")
    cr.add_argument("--root", type=Path, default=Path("."))
    rs = ssub.add_parser("restore")
    rs.add_argument("--id", required=True)
    rs.add_argument("--db", default=None, help="URL del servidor (por defecto DATABASE_URL)")
    rs.add_argument("--root", type=Path, default=Path("."))
    im = ssub.add_parser("import", help="importa un pg_dump EXTERNO en una BD nueva futsal_eval_<id> (nunca toca otra)")
    im.add_argument("--file", type=Path, required=True)
    im.add_argument("--id", required=True)
    im.add_argument("--as-of", required=True, help="ISO; sin zona = Europe/Madrid")
    im.add_argument("--upgrade-copy", action="store_true", help="además restaura futsal_eval_<id>_head y migra solo esa copia a head")
    im.add_argument("--db", default=None, help="URL del servidor (por defecto DATABASE_URL)")
    im.add_argument("--root", type=Path, default=Path("."))
    vf = ssub.add_parser("verify")
    vf.add_argument("--id", required=True)
    vf.add_argument("--db", required=True)
    vf.add_argument("--root", type=Path, default=Path("."))
    return ap


def _read(path: Path) -> dict[str, Any]:
    target = path / "results.json" if path.is_dir() else path
    data: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    return data


def cmd_plan(a: argparse.Namespace) -> int:
    counts = None
    if a.suite == "regression" and a.db:
        from sqlalchemy import create_engine

        from futsal.agent.evaluate import build_cases, load_truth
        engine = create_engine(a.db)
        truth = load_truth(engine)
        engine.dispose()
        if truth is not None:
            cases = build_cases(truth)
            counts = {"cases": len(cases), "evaluable": sum(c.facts is not None for c in cases)}
    plan = planning.build_plan(a.suite, a.split, a.reps, a.root, a.calibrate, a.provider, a.model, a.max_requests, counts)
    print(planning.render(plan))
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    if not a.confirm_live:
        print("run llama al LLM real (de pago): añade --confirm-live tras aprobar el presupuesto (`plan` lo estima sin llamar a nada)",
              file=sys.stderr)
        return 1
    if a.split in ("holdout", "all"):
        problems = verify_seals(a.root)
        if problems:
            print("holdout no íntegro: " + "; ".join(problems), file=sys.stderr)
            return 1
    from futsal.agent.llm import create_llm
    cfg = RunConfig(suite=a.suite, snapshot=a.snapshot, output=a.output, split=a.split, reps=a.reps, max_requests=a.max_requests,
                    min_interval=a.min_interval, fill_missing=a.fill_missing, arm=a.arm, root=a.root, db_url=a.db,
                    admin_url=a.admin_db or database_url(), progress=lambda m: print(m, flush=True))
    try:
        results = run_eval(cfg, create_llm)
    except FingerprintMismatch as exc:
        print(f"NO SE EJECUTA: {exc}", file=sys.stderr)
        return EXIT_FINGERPRINT
    print(f"results.json y report.md en {a.output}")
    return QUOTA_EXIT_CODE if results["meta"]["stopped"] else 0


def cmd_report(a: argparse.Namespace) -> int:
    results = _read(a.output)
    results["summary"] = reporting.summarize(results)
    (a.output / "report.md").write_text(reporting.render_markdown(results), encoding="utf-8")
    print(f"report.md regenerado en {a.output}")
    return 0


def cmd_regrade(a: argparse.Namespace) -> int:
    from futsal.evals import regression
    from futsal.evals.runner import write_outputs
    results = _read(a.run)
    if results["meta"].get("suite") != "regression":
        print("regrade solo admite ejecuciones de la suite regression", file=sys.stderr)
        return 1
    if results["meta"]["snapshot"]["id"] != a.snapshot:
        print(f"la ejecución es del snapshot «{results['meta']['snapshot']['id']}», no de «{a.snapshot}»", file=sys.stderr)
        return 1
    out = a.output or a.run.with_name(a.run.name + "-regraded")
    if out.exists():
        print(f"{out} ya existe: regrade nunca sobrescribe", file=sys.stderr)
        return 1
    cases: dict[str, Any] = {}
    truth = None
    if a.db:
        from sqlalchemy import create_engine

        from futsal.agent.evaluate import build_cases, load_truth
        ok, detail = snaps.verify_db(snaps.load_manifest(a.snapshot, a.root), a.db)
        if not ok:
            print(f"NO SE RECALIFICA: la BD no es el snapshot «{a.snapshot}»: {detail}", file=sys.stderr)
            return EXIT_FINGERPRINT
        if any(r.get("evidence") for r in results["records"]):
            engine = create_engine(a.db)
            try:
                truth = load_truth(engine)
            finally:
                engine.dispose()
            if truth is not None:
                cases = {c.id: c for c in build_cases(truth)}
    new, diff = regression.regrade_results(results, cases, truth, str(a.run))
    write_outputs(out, new)
    (out / "verdict-diff.json").write_text(json.dumps(diff, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    g0, g1 = results["summary"]["global"]["accuracy"], new["summary"]["global"]["accuracy"]
    print(f"recalificado en {out}: {len(diff)} veredictos cambian · exactitud {g0['k']}/{g0['n']} -> {g1['k']}/{g1['n']} · "
          f"parciales {new['meta']['regrade']['partial']} · completos {new['meta']['regrade']['full']} · llamadas al LLM 0")
    for d in diff:
        print(f"  {d['case']}#{d['rep']}: {d['before']} -> {d['after']} ({d['mode']})")
    return 0


def cmd_compare(a: argparse.Namespace) -> int:
    try:
        result = comparing.compare(_read(a.a), _read(a.b), _read(a.noise) if a.noise else None)
    except comparing.IncomparableRuns as exc:
        print(f"NO COMPARABLE: {exc}", file=sys.stderr)
        return 1
    print(comparing.render(result))
    return int(result["exit_code"])


def cmd_snapshot(a: argparse.Namespace) -> int:
    url = a.db or database_url()
    if a.action == "create":
        manifest = snaps.create_snapshot(a.id, a.as_of, url, a.root, a.synthetic)
        print(f"snapshot {a.id}: huella {manifest['fingerprint']['sha256'][:12]} · volcado {manifest['dump_sha256'][:12]} · {manifest['counts']}")
        return 0
    if a.action == "import":
        try:
            m = snaps.import_snapshot(a.file, a.id, a.as_of, url, a.root, a.upgrade_copy)
        except snaps.DatabaseExists as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_FINGERPRINT
        print(f"importado {a.id} en {m['restored_db']}: revisión {m['alembic_revision']} · huella {m['fingerprint']['sha256'][:12]} · "
              f"tablas que faltan: {m['tables_missing'] or 'ninguna'} · {m['counts']}")
        if "upgrade_copy" in m:
            u = m["upgrade_copy"]
            print(f"copia {u['restored_db']}: {u['alembic_revision_before']} -> {u['alembic_revision_after']} · huella "
                  f"{u['fingerprint_after']['sha256'][:12]} · tablas previas idénticas")
        return 0
    if a.action == "restore":
        restored = snaps.restore_snapshot(a.id, url, a.root)
        print(f"{restored.name}: {restored.detail}\n{restored.url}")
        return 0 if restored.ok else EXIT_FINGERPRINT
    ok, detail = snaps.verify_db(snaps.load_manifest(a.id, a.root), url)
    print(detail)
    return 0 if ok else EXIT_FINGERPRINT


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return {"plan": cmd_plan, "run": cmd_run, "report": cmd_report, "regrade": cmd_regrade, "compare": cmd_compare, "snapshot": cmd_snapshot}[args.cmd](args)
    except (snaps.SnapshotError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
