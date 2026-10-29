"""Comandos CLI de actualización y consistencia (Fase 21): refresh, refresh-status, refresh-reconcile y refresh-recover.

Son operaciones ADMINISTRATIVAS: ni la API ni el MCP exponen ninguna que descargue o escriba (solo consultan la frescura).
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from futsal.config.targets import RFFM_TARGET
from futsal.ingestion.rffm.catalog_ingest import estimate_requests, group_dir, iter_groups, plan_update
from futsal.ingestion.rffm.client import PoliteFetcher
from futsal.ingestion.rffm.update_policy import UpdatePolicy
from futsal.refresh.freshness import DATASETS

CATALOG_DIR = Path("data/rffm/catalog")
TABLES_DIR = Path("data/rffm/tables")
REPORT_DIR = Path("data/rffm/match-reports")
COMMANDS = ("refresh", "refresh-status", "refresh-reconcile", "refresh-recover")
OK_RUN_STATES = ("success", "no_changes")


def add_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    def scope(p: argparse.ArgumentParser, *, required_scope: bool = True) -> None:
        p.add_argument("--season", default=RFFM_TARGET["season"])
        p.add_argument("--game-type", default=RFFM_TARGET["game_type"])
        p.add_argument("--catalog-dir", type=Path, default=CATALOG_DIR, help="catálogo y snapshots de partidos (discover-catalog)")
        p.add_argument("--competition", action="append", default=None, metavar="ID", help="ID real de competición (repetible)")
        p.add_argument("--group", action="append", default=None, metavar="ID", help="ID real de grupo (repetible)")
        if required_scope:
            p.add_argument("--all-groups", action="store_true", help="todos los grupos del inventario (sin él hay que indicar --competition o --group)")

    p = sub.add_parser("refresh", help="actualiza partidos, actas, clasificaciones y goleadores de forma segura e incremental")
    scope(p)
    p.add_argument("--only", default=",".join(DATASETS), help=f"tipos de dato separados por comas: {', '.join(DATASETS)} (por defecto todos)")
    p.add_argument("--mode", choices=("incremental", "full"), default="incremental",
                   help="incremental: solo lo necesario (política de --update); full: vuelve a pedir y contrastar todo el alcance")
    p.add_argument("--dry-run", action="store_true", help="descarga y compara pero NO escribe en PostgreSQL (no descarga actas)")
    p.add_argument("--offline", action="store_true", help="solo usa los snapshots locales (sin red)")
    p.add_argument("--plan", action="store_true", help="solo estima las peticiones de partidos; sin red ni base de datos")
    p.add_argument("--tables-dir", type=Path, default=TABLES_DIR)
    p.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    p.add_argument("--output", type=Path, default=None, help="guarda el informe completo en un JSON")
    p.add_argument("--request-delay", type=float, default=2.0)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--table-max-age-hours", type=float, default=12.0, help="(incremental) una tabla ingerida hace menos de N horas no se repite")
    p.add_argument("--report-limit", type=int, default=None, help="máximo de actas por grupo y ejecución")
    p.add_argument("--regression-confirm-hours", type=float, default=24.0,
                   help="horas mínimas entre dos ejecuciones para aceptar que un partido finalizado ya no tiene resultado (24)")
    p.add_argument("--horizon-days", type=int, default=UpdatePolicy.horizon_days)
    p.add_argument("--settle-days", type=int, default=UpdatePolicy.settle_days)
    p.add_argument("--max-age-days", type=int, default=UpdatePolicy.max_age_days)
    p.add_argument("--min-age-hours", type=float, default=UpdatePolicy.min_age_hours)

    p = sub.add_parser("refresh-status", help="últimas ejecuciones y frescura de un grupo o competición (solo lectura)")
    p.add_argument("--season", default=RFFM_TARGET["season"])
    p.add_argument("--limit", type=int, default=5, help="número de ejecuciones a mostrar")
    p.add_argument("--competition", default=None, metavar="ID", help="ID real de competición: frescura de todos sus grupos")
    p.add_argument("--group", default=None, metavar="ID", help="ID real de grupo: frescura detallada")
    p.add_argument("--abandon-hours", type=float, default=None, help="antigüedad a partir de la cual una ejecución running se señala como abandonada")

    p = sub.add_parser("refresh-reconcile", help="compara snapshots locales de la fuente con PostgreSQL (solo lectura, sin red)")
    scope(p)
    p.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    p.add_argument("--json", type=Path, default=None, help="guarda el informe completo")
    p.add_argument("--fail-on-differences", action="store_true", help="código de salida 1 si hay diferencias, inconsistencias o errores de ingesta")

    p = sub.add_parser("refresh-recover", help="marca como abandonadas las ejecuciones running antiguas (idempotente)")
    p.add_argument("--max-age-hours", type=float, default=None, help="antigüedad mínima (por defecto FUTSAL_RUN_ABANDON_HOURS o 6)")
    p.add_argument("--dry-run", action="store_true", help="solo lista las que se recuperarían")


def _load_inventory(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    work = Path(args.catalog_dir) / f"s{args.season}_t{args.game_type}"
    path = work / "inventory.json"
    if not path.exists():
        raise RuntimeError(f"no existe {path}; ejecute antes: python -m futsal.cli discover-catalog")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return work, data


def _scope(args: argparse.Namespace) -> tuple[set[str] | None, set[str] | None]:
    competitions = set(args.competition) if args.competition else None
    groups = set(args.group) if args.group else None
    if not (competitions or groups or getattr(args, "all_groups", False)):
        raise RuntimeError("indique el alcance: --competition ID, --group ID o --all-groups")
    return competitions, groups


def run(args: argparse.Namespace) -> int:
    try:
        if args.command == "refresh":
            return _refresh(args)
        if args.command == "refresh-status":
            return _status(args)
        if args.command == "refresh-reconcile":
            return _reconcile(args)
        return _recover(args)
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
        return 2


def _refresh(args: argparse.Namespace) -> int:
    from futsal.refresh.service import RefreshOptions, UnknownScope, dump_report, filter_inventory, run_refresh
    from futsal.repositories.league import RegressionPolicy

    competitions, groups = _scope(args)
    work, inventory = _load_inventory(args)
    datasets = tuple(d.strip() for d in args.only.split(",") if d.strip())
    policy = UpdatePolicy(args.horizon_days, args.settle_days, args.max_age_days, args.min_age_hours)
    try:
        scoped = filter_inventory(inventory, competitions, groups)
    except UnknownScope as exc:
        raise RuntimeError(str(exc)) from exc
    if args.plan:
        plan = (plan_update(scoped, work, policy, datetime.now().astimezone()) if args.mode == "incremental"
                else estimate_requests(scoped, work))
        print(json.dumps({"mode": args.mode, "datasets": datasets, **plan,
                          "note": "solo partidos; las actas, clasificaciones y goleadores añaden como máximo una petición por elemento"},
                         ensure_ascii=False))
        return 0
    from futsal.db.session import make_engine

    engine = make_engine()
    fetcher = PoliteFetcher(delay=args.request_delay, timeout=args.timeout)
    opts = RefreshOptions(datasets=datasets, mode=args.mode, dry_run=args.dry_run, offline=args.offline, update_policy=policy,
                          regression=RegressionPolicy(args.regression_confirm_hours), table_max_age_hours=args.table_max_age_hours,
                          report_limit=args.report_limit)
    work_tables = Path(args.tables_dir) / f"s{args.season}_t{args.game_type}"
    n_groups = len(iter_groups(scoped))
    print(f"Actualización {args.mode}{' (dry-run: no se escribe en PostgreSQL)' if args.dry_run else ''}: {n_groups} grupos, "
          f"tipos de dato: {', '.join(datasets)}.", file=sys.stderr)

    def show(item: dict[str, Any]) -> None:
        parts = []
        for name, d in item["datasets"].items():
            counts = d.get("counts") or {}
            extra = (f"(+{counts['inserted']} ~{counts['updated']} ={counts['unchanged']})" if name == "matches" and "inserted" in counts else "")
            parts.append(f"{name}={d['status']}{extra}")
        print(f"{item['competition_id']}/{item['group_id']} " + " ".join(parts))
        for name, d in item["datasets"].items():
            if d.get("message") and d["status"] not in OK_RUN_STATES:
                print(f"    {name}: {d['message']}")

    report = run_refresh(engine, inventory, work, work_tables, Path(args.report_dir), fetcher, opts, competitions=competitions,
                         groups=groups, requests_made=lambda: fetcher.requests, on_group=show)
    if args.output:
        dump_report(args.output, report)
    print(f"Resultado: {report['status']} · ejecución {report['run_id'] or '(dry-run, sin registrar)'} · "
          f"peticiones HTTP={report['http_requests']}" + (f" · {report['warning']}" if report.get("warning") else "")
          + (f" · error: {report['error']}" if report.get("error") else ""))
    return 0 if report["status"] in OK_RUN_STATES else 1


def _status(args: argparse.Namespace) -> int:
    from futsal.db.models import Competition, CompetitionGroup
    from futsal.db.session import make_engine
    from futsal.refresh.freshness import competition_freshness, group_freshness
    from futsal.refresh.runs import abandon_hours, recent_runs

    engine = make_engine()
    runs = recent_runs(engine, args.limit)
    limit = abandon_hours(args.abandon_hours)
    now = datetime.now(UTC)
    print(f"Últimas {len(runs)} ejecuciones:")
    for r in runs:
        stuck = r["status"] == "running" and (now - datetime.fromisoformat(r["started_at"])).total_seconds() > limit * 3600
        print(f"  #{r['run_id']} {r['kind']:<8} {r['status']:<10} {r['started_at'][:16]} +{r['inserted']} ~{r['updated']} ={r['unchanged']} "
              f"incidencias={r['issues']}" + (f" error={r['error']}" if r["error"] else "")
              + ("  <- posiblemente abandonada (refresh-recover)" if stuck else ""))
    with Session(engine) as db:
        if args.group:
            gid = db.scalar(select(CompetitionGroup.id).where(CompetitionGroup.external_id == args.group))
            data = None if gid is None else group_freshness(db, int(gid))
            if data is None:
                raise RuntimeError(f"el grupo {args.group} no está en PostgreSQL")
            _print_group(data)
        elif args.competition:
            cid = db.scalar(select(Competition.id).where(Competition.external_id == args.competition))
            comp = None if cid is None else competition_freshness(db, int(cid))
            if comp is None:
                raise RuntimeError(f"la competición {args.competition} no está en PostgreSQL")
            for g in comp["groups"]:
                states = " ".join(f"{k}={v['status']}" for k, v in g["datasets"].items())
                print(f"  grupo {g['group_id']} {g['group']}: {states}")
                for w in g["warnings"]:
                    print(f"      ! {w}")
    return 0


def _print_group(data: dict[str, Any]) -> None:
    print(f"Frescura del grupo {data['group']} ({data['competition']}), a {data['as_of'][:16]}:")
    for name, d in data["datasets"].items():
        last = d.get("last_attempt") or {}
        print(f"  {name:<10} {d['status']:<14} válida={str(d.get('last_valid_at', '-'))[:16]} fuente={str(d.get('source_fetched_at', '-'))[:16]}"
              f" último intento={last.get('status', '-')} ({str(last.get('at', '-'))[:16]})")
    for w in data["warnings"]:
        print(f"  ! {w}")
    print(data["note"])


def _reconcile(args: argparse.Namespace) -> int:
    from futsal.db.session import make_engine
    from futsal.refresh.reconcile import reconcile

    competitions, groups = _scope(args)
    work, inventory = _load_inventory(args)
    pairs = [(c["external_id"], g["external_id"]) for c, g in iter_groups(inventory, competitions) if not groups or g["external_id"] in groups]
    if not pairs:
        raise RuntimeError("ningún grupo coincide con --competition/--group")
    sources: list[tuple[str, str, Path | None]] = [(c, g, group_dir(work, c, g) / "league.json") for c, g in pairs]
    rep = reconcile(make_engine(), sources, report_dir=Path(args.report_dir))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rep, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print("Reconciliación (solo lectura; no se ha modificado nada): " + ", ".join(f"{k}={v}" for k, v in rep["totals"].items()))
    for g in rep["groups"]:
        counts = {k: v for k, v in g["counts"].items() if v}
        print(f"{g['competition_id']}/{g['group_id']} " + (", ".join(f"{k}={v}" for k, v in counts.items()) or "sin diferencias"))
        for kind, n in g["kinds"].items():
            print(f"    {kind}: {n}")
    problems = sum(rep["totals"][k] for k in ("confirmed_difference", "possible_inconsistency", "ingest_error"))
    return 1 if args.fail_on_differences and problems else 0


def _recover(args: argparse.Namespace) -> int:
    from futsal.db.session import make_engine
    from futsal.refresh.runs import abandon_hours, recover_abandoned

    hours = abandon_hours(args.max_age_hours)
    found = recover_abandoned(make_engine(), max_age_hours=hours, dry_run=args.dry_run)
    verb = "se recuperarían" if args.dry_run else "marcadas como abandoned"
    print(f"{len(found)} ejecución(es) running con más de {hours:g} h: {verb}.")
    for r in found:
        print(f"  #{r['run_id']} {r['kind']} iniciada {r['started_at'][:16]} ({r['age_hours']} h)")
    return 0
