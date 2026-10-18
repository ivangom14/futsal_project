"""Comandos CLI de la ingesta por catálogo (Fase 14): discover-catalog, scrape-catalog, import-catalog, verify-catalog."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from futsal.config.targets import CATALOG_SCOPE, RFFM_TARGET
from futsal.ingestion.rffm.catalog import discover_catalog
from futsal.ingestion.rffm.catalog_ingest import REPORT_NAME, estimate_requests, scrape_catalog
from futsal.ingestion.rffm.client import PoliteFetcher
from futsal.ingestion.rffm.league import Target
from futsal.ingestion.rffm.progress import Progress

CATALOG_DIR = Path("data/rffm/catalog")
COMMANDS = ("discover-catalog", "scrape-catalog", "import-catalog", "verify-catalog")


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
    p = sub.add_parser("import-catalog", help="importa en PostgreSQL los grupos descargados")
    common(p, network=False)
    p.add_argument("--dry-run", action="store_true")
    common(sub.add_parser("verify-catalog", help="compara lo descargado con PostgreSQL (solo lectura)"), network=False)


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


def run(args: argparse.Namespace) -> int:
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
            if args.plan:
                print(json.dumps(estimate_requests(inv, work, only), ensure_ascii=False))
                return 0
            fetcher = PoliteFetcher(delay=args.request_delay, timeout=args.timeout)
            plan = estimate_requests(inv, work, only)
            progress = Progress(plan["estimated_requests"], plan["groups"], lambda: fetcher.requests)
            print(f"Descarga: {plan['groups']} grupos, ~{plan['estimated_requests']} peticiones previstas "
                  f"(pausa {args.request_delay:g} s entre peticiones).", file=sys.stderr)
            rep = scrape_catalog(inv, work, fetcher, only=only, refresh=args.refresh, resume=args.resume,
                                 max_rounds=args.max_rounds, requests_made=lambda: fetcher.requests, progress=progress)
            progress.finish()
            for g in rep["groups"]:
                print(f"{g['competition_id']}/{g['group_id']} {g['status']} "
                      f"jornadas={g.get('rounds_discovered', '-')} partidos={g.get('matches_total', '-')} "
                      f"peticiones={g['http_requests']}" + (f" error={g['error']}" if g.get("error") else ""))
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
