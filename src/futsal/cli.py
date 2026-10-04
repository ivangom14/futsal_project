"""CLI: `python -m futsal.cli {inspect-rffm,scrape-round,list-rounds,scrape-league}`."""

import argparse
import re
from pathlib import Path

from futsal.config.targets import RFFM_INITIAL_ROUND, RFFM_TARGET
from futsal.ingestion.rffm.client import PoliteFetcher, read_or_fetch, round_url
from futsal.ingestion.rffm.export import write_json
from futsal.ingestion.rffm.league import Target, discover_rounds, scrape_league
from futsal.ingestion.rffm.parser import RffmParseError, parse_round

SNAPSHOT_DIR = Path("data/raw/rffm")
LEAGUE_DIR = Path("data/rffm")


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--round", type=int, default=RFFM_INITIAL_ROUND)
    for key, value in RFFM_TARGET.items():
        p.add_argument(f"--{key.replace('_', '-')}", default=value)
    p.add_argument("--refresh", action="store_true", help="forzar nueva descarga")
    p.add_argument("--snapshot-dir", type=Path, default=SNAPSHOT_DIR)


def _load(args: argparse.Namespace) -> tuple[str, str]:
    url = round_url(args.season, args.competition, args.group, args.round, args.game_type)
    name = re.sub(r"\W+", "_", f"s{args.season}_c{args.competition}_g{args.group}_r{args.round}")
    return url, read_or_fetch(url, args.snapshot_dir / f"{name}.html", args.refresh)


def _league_args(p: argparse.ArgumentParser) -> None:
    for key, value in RFFM_TARGET.items():
        p.add_argument(f"--{key.replace('_', '-')}", default=value)
    p.add_argument("--output", type=Path, default=LEAGUE_DIR)
    p.add_argument("--refresh", action="store_true", help="forzar nueva descarga")
    p.add_argument("--resume", action="store_true", help="omitir jornadas ya completadas")
    p.add_argument("--request-delay", type=float, default=2.0)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--max-rounds", type=int, default=None)


def _league(args: argparse.Namespace) -> int:
    target = Target(args.season, args.competition, args.group, args.game_type)
    fetcher = PoliteFetcher(delay=args.request_delay, timeout=args.timeout)
    seed = str(RFFM_INITIAL_ROUND)
    try:
        if args.command == "list-rounds":
            seed_file = args.output / "rounds" / f"{seed}.html"
            if seed_file.exists() and not args.refresh:
                html = seed_file.read_text(encoding="utf-8")
            else:
                html = fetcher(round_url(args.season, args.competition, args.group, seed, args.game_type))
            refs, issues = discover_rounds(html, target)
            for r in refs[: args.max_rounds]:
                print(f"id={r.external_round_id} visible={r.visible_round_label} fecha={r.scheduled_date}")
            print(f"{len(refs)} jornadas, {len(issues)} incidencias")
            return 0
        s = scrape_league(target, args.output, fetcher, seed, refresh=args.refresh,
                          resume=args.resume, max_rounds=args.max_rounds,
                          requests_made=lambda: fetcher.requests)
    except (RuntimeError, RffmParseError) as exc:
        print(f"Error: {exc}")
        return 2
    print(f"jornadas: {s['rounds_discovered']} descubiertas, {s['rounds_downloaded']} descargadas, "
          f"{s['rounds_from_cache']} caché, {s['rounds_failed']} fallidas; "
          f"partidos={s['matches_total']} peticiones={s['http_requests']} "
          f"incidencias={len(s['quality_issues'])}")
    return 1 if s["rounds_failed"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="futsal")
    sub = parser.add_subparsers(dest="command", required=True)
    _common(sub.add_parser("inspect-rffm", help="resumen de la jornada"))
    scrape = sub.add_parser("scrape-round", help="exporta la jornada a JSON")
    _common(scrape)
    scrape.add_argument("--output", type=Path, required=True)
    for name, help_ in (("list-rounds", "jornadas declaradas por la web"),
                        ("scrape-league", "descarga todas las jornadas")):
        league = sub.add_parser(name, help=help_)
        _league_args(league)
    args = parser.parse_args(argv)
    if args.command in ("list-rounds", "scrape-league"):
        return _league(args)
    url, html = _load(args)
    try:
        data = parse_round(html, url)
    except RffmParseError as exc:
        print(f"Error de parseo: {exc}")
        return 2
    if args.command == "scrape-round":
        write_json(data, args.output)
        print(f"{len(data.matches)} partidos -> {args.output}")
    else:
        print(f"jornada={data.round_number_or_label} fecha={data.round_date} "
              f"partidos={len(data.matches)} grupo={data.group_name}")
        for m in data.matches:
            print(f"  {m.home_team_name} {m.home_score}-{m.away_score} {m.away_team_name} [{m.status}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
