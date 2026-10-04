"""CLI: `python -m futsal.cli {inspect-rffm,scrape-round} --round N`."""

import argparse
import re
from pathlib import Path

from futsal.ingestion.rffm.client import read_or_fetch, round_url
from futsal.ingestion.rffm.export import write_json
from futsal.ingestion.rffm.parser import RffmParseError, parse_round

DEFAULTS = {"season": "22", "competition": "26738243", "group": "26738245", "game_type": "3"}
SNAPSHOT_DIR = Path("data/raw/rffm")


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--round", type=int, required=True)
    for key, value in DEFAULTS.items():
        p.add_argument(f"--{key.replace('_', '-')}", default=value)
    p.add_argument("--refresh", action="store_true", help="forzar nueva descarga")
    p.add_argument("--snapshot-dir", type=Path, default=SNAPSHOT_DIR)


def _load(args: argparse.Namespace) -> tuple[str, str]:
    url = round_url(args.season, args.competition, args.group, args.round, args.game_type)
    name = re.sub(r"\W+", "_", f"s{args.season}_c{args.competition}_g{args.group}_r{args.round}")
    return url, read_or_fetch(url, args.snapshot_dir / f"{name}.html", args.refresh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="futsal")
    sub = parser.add_subparsers(dest="command", required=True)
    _common(sub.add_parser("inspect-rffm", help="resumen de la jornada"))
    scrape = sub.add_parser("scrape-round", help="exporta la jornada a JSON")
    _common(scrape)
    scrape.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
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
