"""Comandos CLI de la ingesta por catálogo (Fase 14): discover-catalog, scrape-catalog, import-catalog, verify-catalog."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from futsal.config.targets import CATALOG_SCOPE, RFFM_TARGET
from futsal.ingestion.rffm.catalog import discover_catalog
from futsal.ingestion.rffm.catalog_ingest import (
    PENDING,
    REPORT_NAME,
    estimate_requests,
    plan_update,
    scrape_catalog,
)
from futsal.ingestion.rffm.client import PoliteFetcher
from futsal.ingestion.rffm.league import Target
from futsal.ingestion.rffm.progress import Progress
from futsal.ingestion.rffm.update_policy import UpdatePolicy

CATALOG_DIR = Path("data/rffm/catalog")
NETWORK_ENV = "FUTSAL_RFFM_NETWORK_AUTHORIZED"
COMMANDS = ("discover-catalog", "scrape-catalog", "import-catalog", "verify-catalog", "catalog-backfill",
            "discover-catalog-v2", "crest-backfill")


def add_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    def common(p: argparse.ArgumentParser, *, network: bool) -> None:
        p.add_argument("--season", default=RFFM_TARGET["season"])
        p.add_argument("--game-type", default=RFFM_TARGET["game_type"])
        p.add_argument("--output", type=Path, default=CATALOG_DIR, help="directorio de trabajo del catálogo")
        p.add_argument("--competition", action="append", default=None, metavar="ID",
                       help="ID real de competición (repetible); sin él, todo el alcance")
        if network:
            p.add_argument("--refresh", action="store_true", help="forzar nueva descarga")
            p.add_argument("--request-delay", type=float, default=2.0)
            p.add_argument("--timeout", type=float, default=30.0)

    common(sub.add_parser("discover-catalog", help="identifica las 14 competiciones y descubre sus grupos"), network=True)
    p = sub.add_parser("scrape-catalog", help="descarga todos los grupos confirmados del inventario")
    common(p, network=True)
    p.add_argument("--plan", action="store_true", help="solo estima peticiones; no descarga")
    p.add_argument("--resume", action="store_true", help="omitir jornadas ya completadas")
    p.add_argument("--max-rounds", type=int, default=None)
    p.add_argument("--update", action="store_true",
                   help="solo vuelve a pedir las jornadas ya descargadas con partidos sin resultado, aplazados o con errores, "
                        "las próximas y las cerradas hace poco; omite las cerradas y asentadas")
    p.add_argument("--horizon-days", type=int, default=UpdatePolicy.horizon_days,
                   help="(--update) partidos por jugar dentro de estos días se revisan siempre (defecto 14)")
    p.add_argument("--settle-days", type=int, default=UpdatePolicy.settle_days,
                   help="(--update) una jornada cerrada se revisa hasta tener un snapshot de N días después de su último partido (3)")
    p.add_argument("--max-age-days", type=int, default=UpdatePolicy.max_age_days,
                   help="(--update) partidos lejanos: se revisan como mucho cada N días (30)")
    p.add_argument("--min-age-hours", type=float, default=UpdatePolicy.min_age_hours,
                   help="(--update) no se repite una jornada descargada hace menos de N horas (12)")
    p = sub.add_parser("import-catalog", help="importa en PostgreSQL los grupos descargados")
    common(p, network=False)
    p.add_argument("--dry-run", action="store_true")
    common(sub.add_parser("verify-catalog", help="compara lo descargado con PostgreSQL (solo lectura)"), network=False)
    p = sub.add_parser("crest-backfill", help="rutas de escudos en teams.crest_url desde las instantáneas ya descargadas (sin red)")
    p.add_argument("--dir", default="data", help="carpeta con instantáneas .html/.json de la RFFM (se recorre entera)")
    p.add_argument("--dry-run", action="store_true", help="calcula los cambios sin guardarlos")
    p = sub.add_parser("catalog-backfill", help="identidad de equipos y clasificación de competiciones ya importadas (sin red)")
    p.add_argument("--dry-run", action="store_true", help="calcula y muestra el resumen sin escribir")
    p.add_argument("--inventory", type=Path, default=None, metavar="PATH",
                   help="inventory.json (o directorio con inventarios); por defecto, los de data/rffm/catalog si existen")
    p.add_argument("--reclassify-confirmed", action="store_true",
                   help="vuelve a clasificar también las competiciones confirmadas (guarda el valor anterior; nunca toca un override)")
    _discover_v2_parser(sub)


def _discover_v2_parser(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    p = sub.add_parser("discover-catalog-v2", help="catálogo oficial por /api/competitions + /api/groups (offline: --from-dir o --plan)")
    p.add_argument("--season", required=True)
    p.add_argument("--game-type", default=RFFM_TARGET["game_type"])
    p.add_argument("--output", type=Path, default=CATALOG_DIR, help="directorio de trabajo del catálogo")
    p.add_argument("--from-dir", type=Path, default=None, metavar="DIR",
                   help="construye el informe con JSON guardados (NN.json + requests.jsonl); sin red")
    p.add_argument("--plan", action="store_true", help="imprime las URLs que se pedirían (1 + C); sin red")
    p.add_argument("--n-competitions", type=int, default=50, help="(--plan sin --from-dir) C estimado (P0: 50)")


def _discover_v2(args: argparse.Namespace) -> int:
    import os

    from futsal.ingestion.rffm.discovery_v2 import diff, discover, fetch_from_dir, plan_requests

    if args.plan:
        ids = None
        if args.from_dir:
            report = discover(args.season, args.game_type, fetch_from_dir(args.from_dir))
            ids = [c["competition_id"] for c in report["competitions"]]
        for url in plan_requests(args.season, len(ids) if ids is not None else args.n_competitions, ids, args.game_type):
            print(url)
        return 0
    if args.from_dir is None:
        if os.environ.get(NETWORK_ENV) != "1":
            print(f"Error: sin --from-dir ni --plan se usaría la red de la RFFM; rechazado (requiere {NETWORK_ENV}=1 y autorización).")
        else:
            print("Error: la descarga real de discover-catalog-v2 no está implementada (T05 es solo offline).")
        return 2
    work = _work_dir(args)
    report = discover(args.season, args.game_type, fetch_from_dir(args.from_dir))
    target = work / REPORT_NAME
    if target.exists():  # conserva el estado de descarga de los grupos ya conocidos y muestra los cambios
        previous = _load(target)
        known = {(g["competition_id"], g["group_id"]): g for g in previous.get("groups", [])}
        for g in report["groups"]:
            old = known.get((g["competition_id"], g["group_id"]))
            if old is not None and old.get("status") != PENDING:
                g.update({k: v for k, v in old.items() if k not in g or k in ("status", "league_path")})
        if "competitions" in previous:
            d = diff(previous, report)
            print(f"cambios: altas={len(d.competitions_added)} bajas={len(d.competitions_removed)} "
                  f"grupos+={len(d.groups_added)} grupos-={len(d.groups_removed)} otros={len(d.changes)}")
    _dump(target, report)
    print(f"{report['summary']} -> {target}")
    return 0


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"no existe {path}; ejecute antes el comando previo")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _work_dir(args: argparse.Namespace) -> Path:
    return Path(args.output) / f"s{args.season}_t{args.game_type}"


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _backfill(args: argparse.Namespace) -> int:
    from futsal.db.session import make_engine
    from futsal.importer.catalog_backfill import find_inventories, run_backfill

    paths = find_inventories(args.inventory, CATALOG_DIR)
    summary = run_backfill(make_engine(), inventories=paths, dry_run=args.dry_run, reclassify_confirmed=args.reclassify_confirmed)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def run(args: argparse.Namespace) -> int:
    if args.command == "discover-catalog-v2":
        try:
            return _discover_v2(args)
        except Exception as exc:  # noqa: BLE001 - la CLI informa en una línea sin traza
            print(f"Error: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
            return 2
    if args.command == "crest-backfill":
        try:
            from futsal.db.session import make_engine
            from futsal.importer.crest_backfill import run_crest_backfill

            print(json.dumps(run_crest_backfill(make_engine(), Path(args.dir), dry_run=args.dry_run), sort_keys=True))
            return 0
        except Exception as exc:  # noqa: BLE001 - la CLI informa en una línea sin traza
            print(f"Error: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
            return 2
    if args.command == "catalog-backfill":
        try:
            return _backfill(args)
        except Exception as exc:  # noqa: BLE001 - la CLI informa en una línea sin traza
            print(f"Error: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
            return 2
    work = _work_dir(args)
    only = set(args.competition) if args.competition else None
    try:
        if args.command == "discover-catalog":
            fetcher = PoliteFetcher(delay=args.request_delay, timeout=args.timeout)
            ref = Target(args.season, RFFM_TARGET["competition"], RFFM_TARGET["group"], args.game_type)
            inv = discover_catalog(ref, CATALOG_SCOPE, fetcher, work, refresh=args.refresh)
            _dump(work / "inventory.json", inv)
            for e in inv["entries"]:
                c = e["competition"] or {}
                groups = ", ".join(f"{g['external_id']}({g['name']})" for g in e["groups"])
                print(f"[{e['identification']}] {e['requested_label']} -> {c.get('external_id', '-')} "
                      f"grupos={groups or e['groups_status']}")
            print(f"{inv['summary']} peticiones={fetcher.requests}")
            return 0
        inv = _load(work / "inventory.json")
        if args.command == "scrape-catalog":
            policy = None
            if args.update:
                if args.refresh:
                    raise RuntimeError("--update y --refresh son incompatibles: --refresh vuelve a pedir TODO")
                policy = UpdatePolicy(args.horizon_days, args.settle_days, args.max_age_days, args.min_age_hours)
            if args.plan:
                if policy is not None:
                    print(json.dumps(plan_update(inv, work, policy, datetime.now().astimezone(), only), ensure_ascii=False))
                else:
                    print(json.dumps(estimate_requests(inv, work, only), ensure_ascii=False))
                return 0
            fetcher = PoliteFetcher(delay=args.request_delay, timeout=args.timeout)
            plan = (plan_update(inv, work, policy, datetime.now().astimezone(), only) if policy is not None
                    else estimate_requests(inv, work, only))
            progress = Progress(plan["estimated_requests"], plan["groups"], lambda: fetcher.requests)
            print(f"{'Actualización' if policy else 'Descarga'}: {plan['groups']} grupos, ~{plan['estimated_requests']} peticiones "
                  f"previstas (pausa {args.request_delay:g} s entre peticiones)."
                  + (f" Motivos: {plan['by_reason']}; jornadas cerradas omitidas: {plan['rounds_skipped_closed']}." if policy else ""),
                  file=sys.stderr)
            rep = scrape_catalog(inv, work, fetcher, only=only, refresh=args.refresh, resume=args.resume or policy is not None,
                                 max_rounds=args.max_rounds, requests_made=lambda: fetcher.requests, progress=progress,
                                 update=policy)
            progress.finish()
            for g in rep["groups"]:
                up = g.get("update") or {}
                extra = (f" revisadas={len(up.get('refetched', {}))} omitidas={up.get('skipped_closed', 0)}"
                         + (f" conservadas_sin_actualizar={len(up['kept_stale'])}" if up.get("kept_stale") else "")) if up else ""
                print(f"{g['competition_id']}/{g['group_id']} {g['status']} "
                      f"jornadas={g.get('rounds_discovered', '-')} partidos={g.get('matches_total', '-')} "
                      f"peticiones={g['http_requests']}{extra}" + (f" error={g['error']}" if g.get("error") else ""))
            return 0 if all(g["status"] == "downloaded" for g in rep["groups"]) else 1
        report = _load(work / REPORT_NAME)
        from futsal.db.session import make_engine
        from futsal.importer.catalog_service import import_catalog, verify_catalog

        engine = make_engine()
        if args.command == "import-catalog":
            results = import_catalog(engine, report, dry_run=args.dry_run, only=only)
            _dump(work / ("catalog-import-dry-run.json" if args.dry_run else "catalog-import-report.json"), results)
            for r in results:
                print(f"{r['competition_id']}/{r['group_id']} {r['import_status']} "
                      f"partidos={r.get('matches', '-')} incidencias={r.get('issues', '-')}"
                      + (f" error={r['error']}" if r.get("error") else ""))
            return 0 if all(r["import_status"] in ("success", "skipped") for r in results) else 1
        out = verify_catalog(engine, report)
        _dump(work / "catalog-verify-report.json", out)
        print(json.dumps(out["integrity"], ensure_ascii=False))
        for g in out["groups"]:
            print(f"{g['competition_id']}/{g['group_id']} persistido={g['persisted']} db={g.get('db')}")
        return 0 if not any(out["integrity"].values()) else 1
    except Exception as exc:  # noqa: BLE001 - la CLI informa en una línea sin traza
        print(f"Error: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
        return 2
