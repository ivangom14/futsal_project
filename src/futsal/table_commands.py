"""Comandos CLI de las tablas oficiales de la RFFM (Fase 16): ingest-standings, ingest-scorers y tables-report."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from futsal.config.targets import RFFM_TARGET
from futsal.ingestion.rffm.catalog_ingest import iter_groups
from futsal.ingestion.rffm.client import PoliteFetcher
from futsal.ingestion.rffm.progress import Progress

CATALOG_DIR = Path("data/rffm/catalog")
TABLES_DIR = Path("data/rffm/tables")
COMMANDS = ("ingest-standings", "ingest-scorers", "tables-report")
OK_STATES = ("ok", "empty", "not_applicable", "skipped")


def add_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--season", default=RFFM_TARGET["season"])
        p.add_argument("--game-type", default=RFFM_TARGET["game_type"])
        p.add_argument("--catalog-dir", type=Path, default=CATALOG_DIR, help="catálogo de competiciones y grupos (discover-catalog)")
        p.add_argument("--output", type=Path, default=TABLES_DIR, help="directorio de caché e informes de las tablas")
        p.add_argument("--competition", action="append", default=None, metavar="ID", help="ID real de competición (repetible)")
        p.add_argument("--group", action="append", default=None, metavar="ID", help="ID real de grupo (repetible)")

    def network(p: argparse.ArgumentParser) -> None:
        p.add_argument("--limit-groups", type=int, default=None, help="procesa solo los N primeros grupos (pruebas)")
        p.add_argument("--refresh", action="store_true", help="ignora la caché y vuelve a pedir")
        p.add_argument("--resume", action="store_true", help="omite lo ya ingerido correctamente dentro de --max-age-hours")
        p.add_argument("--max-age-hours", type=float, default=12.0, help="una página cacheada o un intento correcto más reciente no se repite")
        p.add_argument("--offline", action="store_true", help="solo usa la caché local (sin red)")
        p.add_argument("--dry-run", action="store_true", help="descarga y analiza, pero no escribe en PostgreSQL")
        p.add_argument("--plan", action="store_true", help="solo muestra los grupos y las peticiones previstas")
        p.add_argument("--request-delay", type=float, default=2.0)
        p.add_argument("--timeout", type=float, default=30.0)
        p.add_argument("--null-retries", type=int, default=2, help="reintentos si la web devuelve la tabla en null (fallo intermitente)")

    p = sub.add_parser("ingest-standings", help="clasificaciones oficiales de la RFFM → PostgreSQL")
    common(p)
    network(p)
    p.add_argument("--round", type=int, default=None, help="jornada concreta (la web debe devolver esa misma jornada)")
    p.add_argument("--all-rounds", action="store_true", help="además de la actual, las jornadas 1..actual-1 (una petición por jornada)")
    p = sub.add_parser("ingest-scorers", help="tablas oficiales de goleadores de la RFFM → PostgreSQL")
    common(p)
    network(p)
    p = sub.add_parser("tables-report", help="cobertura de clasificaciones y goleadores en PostgreSQL (solo lectura)")
    common(p)
    p.add_argument("--json", type=Path, default=None, help="guarda el informe completo en un fichero JSON")


def _work(args: argparse.Namespace) -> Path:
    return Path(args.output) / f"s{args.season}_t{args.game_type}"


def _inventory(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.catalog_dir) / f"s{args.season}_t{args.game_type}" / "inventory.json"
    if not path.exists():
        raise RuntimeError(f"no existe {path}; ejecute antes: python -m futsal.cli discover-catalog")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _expected_requests(args: argparse.Namespace, pairs: list[tuple[dict[str, Any], dict[str, Any]]], kind: str) -> int:
    """Cota superior de peticiones: una por grupo (la caché reciente no pide); con --all-rounds, hasta una por jornada del grupo."""
    all_rounds = kind == "standings" and bool(getattr(args, "all_rounds", False))
    return sum(1 + (int(g.get("expected_rounds") or 0) if all_rounds else 0) for _, g in pairs)


def run(args: argparse.Namespace) -> int:
    only = set(args.competition) if args.competition else None
    groups = set(args.group) if args.group else None
    try:
        inv = _inventory(args)
        if args.command == "tables-report":
            return _report(args, inv, only)
        kind = "standings" if args.command == "ingest-standings" else "scorers"
        pairs = [(c, g) for c, g in iter_groups(inv, only) if not groups or g["external_id"] in groups]
        pairs = pairs[: args.limit_groups] if args.limit_groups else pairs
        if not pairs:
            raise RuntimeError("ningún grupo coincide con --competition/--group")
        if args.plan:
            print(json.dumps({"kind": kind, "groups": len(pairs), "max_requests": _expected_requests(args, pairs, kind),
                              "note": "la caché dentro de --max-age-hours no genera peticiones"}, ensure_ascii=False))
            return 0
        from futsal.importer.table_service import ingest_tables

        engine = None
        if not args.dry_run:
            from futsal.db.session import make_engine

            engine = make_engine()
        fetcher = PoliteFetcher(delay=args.request_delay, timeout=args.timeout)
        total = _expected_requests(args, pairs, kind)
        progress = Progress(total, len(pairs), lambda: fetcher.requests, item_word="tabla")
        print(f"{'Clasificaciones' if kind == 'standings' else 'Goleadores'}: {len(pairs)} grupos, hasta ~{total} peticiones "
              f"(pausa {args.request_delay:g} s; la caché reciente no pide).", file=sys.stderr)
        rep = ingest_tables(engine, kind, inv, _work(args), fetcher, only=only, groups=groups, limit_groups=args.limit_groups,
                            round_number=getattr(args, "round", None), all_rounds=getattr(args, "all_rounds", False),
                            refresh=args.refresh, resume=args.resume, max_age_hours=args.max_age_hours,
                            null_retries=args.null_retries, offline=args.offline, dry_run=args.dry_run,
                            requests_made=lambda: fetcher.requests, progress=progress)
        progress.finish()
        for g in rep["groups"]:
            rounds = g["rounds"]
            shown = rounds[0] if len(rounds) == 1 else None
            detail = (f" jornada={shown.get('returned_round', '-')} filas={shown.get('rows', '-')}" if shown and kind == "standings"
                      else f" filas={shown.get('rows', '-')} goles={shown.get('goals_total', '-')}" if shown
                      else f" jornadas={len(rounds)}")
            print(f"{g['competition_id']}/{g['group_id']} {g['status']}{detail} peticiones={g['http_requests']}"
                  + (f" error={g['error']}" if g.get("error") else ""))
        return 0 if all(g["status"] in OK_STATES for g in rep["groups"]) else 1
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return 2


def _report(args: argparse.Namespace, inv: dict[str, Any], only: set[str] | None) -> int:
    from futsal.db.session import make_engine
    from futsal.importer.table_service import coverage

    rep = coverage(make_engine(), inv, only)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rep, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"grupos={rep['groups']} competiciones={rep['competitions']}")
    for kind, states in rep["by_status"].items():
        print(f"{kind}: " + ", ".join(f"{k}={v}" for k, v in sorted(states.items())))
    for r in rep["detail"]:
        s, g = r["standings"], r["scorers"]
        print(f"{r['competition_id']}/{r['group_id']} {r['group']}: clasificación={s['status']}"
              + (f" (jornada {s['round']}, {s['rows']}/{r['expected_teams']} equipos, captura {s['fetched_at'][:16]})" if "round" in s else "")
              + f" | goleadores={g['status']}" + (f" ({g['rows']} jugadores, {g['goals_total']} goles, captura {g['fetched_at'][:16]})"
                                                    if "goals_total" in g else ""))
    bad = any(r[k]["status"] in ("failed", "incomplete", "not_ingested", "no_group_in_db") for r in rep["detail"] for k in ("standings", "scorers"))
    return 1 if bad else 0
