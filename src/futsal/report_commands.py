"""Comandos CLI de actas: inspect/scrape/preview/import/summary."""

import argparse
import json
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from futsal.config.targets import RFFM_TARGET
from futsal.db.session import make_engine
from futsal.importer.report_service import (
    comparison_with_db,
    read_report,
    report_summary,
    run_report_import,
)
from futsal.ingestion.rffm.client import PoliteFetcher, match_report_url, read_or_fetch
from futsal.ingestion.rffm.parser import RffmParseError
from futsal.ingestion.rffm.report_parser import parse_match_report
from futsal.ingestion.rffm.report_preview import render_preview
from futsal.report_batch import process_reports
from futsal.repositories.match_reports import ReportImportError, load_match, select_candidate

REPORT_DIR = Path("data/rffm/match-reports")
COMMANDS = ("inspect-match-report", "scrape-match-report", "preview-match-report",
            "import-match-report", "match-report-summary", "process-match-reports")


def add_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    for name in ("inspect-match-report", "scrape-match-report"):
        p = sub.add_parser(name, help="acta de un partido (sin --match-id elige uno finalizado)")
        p.add_argument("--match-id", default=None)
        p.add_argument("--refresh", action="store_true", help="forzar nueva descarga")
        p.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    p = sub.add_parser("preview-match-report", help="TXT legible desde el JSON normalizado")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--no-db", action="store_true", help="omitir IMPORTACIÓN PREVISTA (sin PostgreSQL)")
    p = sub.add_parser("import-match-report", help="importa el JSON normalizado en PostgreSQL")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("process-match-reports",
                       help="descarga/importa las actas de partidos finalizados sin acta (secuencial)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--request-delay", type=float, default=2.0)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--no-import", action="store_true", help="solo descarga y normaliza")
    p.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    p.add_argument("--output", type=Path, default=None)
    p = sub.add_parser("match-report-summary", help="recuentos agregados de actas")
    p.add_argument("--output", type=Path, default=None)


def _resolve(args: argparse.Namespace) -> tuple[str, str]:
    """(match_id, url). Sin --match-id elige un partido finalizado sin acta importada."""
    engine = make_engine()
    with Session(engine) as session:
        match_id = args.match_id
        if match_id is None:
            cand = select_candidate(session, RFFM_TARGET["group"])
            if cand is None:
                raise RuntimeError("no hay partidos finalizados sin acta importada")
            match_id = cand.external_id
            print(f"partido elegido: {cand.external_id} jornada={cand.round_label} "
                  f"{cand.home} {cand.score} {cand.away} fecha={cand.scheduled_date}")
        loaded = load_match(session, match_id)
        if loaded is None:
            raise RuntimeError(f"el partido {match_id} no existe en PostgreSQL")
        return match_id, match_report_url(match_id, loaded.season_external_id,
                                          loaded.view.competition_external_id,
                                          loaded.view.group_external_id)


def _inspect(args: argparse.Namespace, match_id: str, url: str) -> int:
    raw = args.report_dir / match_id / "raw.html"
    print(f"URL: {url}\nsnapshot: {raw} ({'existe' if raw.exists() else 'no existe; usa scrape-match-report'})")
    if raw.exists():
        r = parse_match_report(raw.read_text(encoding="utf-8"), url)
        print(f"fuente={r.source.technical_source} hash={r.report.content_hash[:12]}")
        print(f"jugadores={len(r.players)} staff={len(r.staff)} oficiales={len(r.officials)} "
              f"eventos={len(r.events)} vacías={r.validation.empty_sections} "
              f"sin interpretar={r.validation.unparsed_sections}")
    return 0


def _scrape(args: argparse.Namespace, match_id: str, url: str) -> int:
    out_dir = args.report_dir / match_id
    html = read_or_fetch(url, out_dir / "raw.html", args.refresh)
    report = parse_match_report(html, url)
    report.validation.comparison = comparison_with_db(make_engine(), report)
    out = out_dir / "normalized.json"
    out.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    diffs = [c.field for c in report.validation.comparison or [] if not c.equal]
    print(f"acta {report.report.report_external_id} -> {out}: jugadores={len(report.players)} "
          f"eventos={len(report.events)} discrepancias={diffs or 'ninguna'}")
    return 0


def run(args: argparse.Namespace) -> int:
    try:
        if args.command in ("inspect-match-report", "scrape-match-report"):
            match_id, url = _resolve(args)
            return (_inspect if args.command == "inspect-match-report" else _scrape)(args, match_id, url)
        if args.command == "process-match-reports":
            fetcher = PoliteFetcher(delay=args.request_delay, timeout=args.timeout)
            summary = process_reports(make_engine(), args.report_dir, fetcher, limit=args.limit,
                                      do_import=not args.no_import,
                                      requests_made=lambda: fetcher.requests)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
            keys = ("candidates", "processed", "downloaded", "from_cache", "imported", "failed",
                    "http_requests", "aborted")
            print(" ".join(f"{k}={summary[k]}" for k in keys))
            return 1 if summary["failed"] else 0
        if args.command == "preview-match-report":
            report = read_report(args.input)
            plan = None
            if not args.no_db:
                try:
                    plan = run_report_import(make_engine(), args.input, dry_run=True).rows() or None
                except (SQLAlchemyError, RuntimeError):
                    plan = None
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(render_preview(report, plan), encoding="utf-8")
            print(f"vista previa -> {args.output}")
            return 0
        if args.command == "import-match-report":
            result = run_report_import(make_engine(), args.input, dry_run=args.dry_run)
            print("\n".join(result.lines()))
            return 0 if result.status == "success" else 1
        text = json.dumps(report_summary(make_engine()), ensure_ascii=False, indent=2, sort_keys=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    except (RuntimeError, RffmParseError, ReportImportError, SQLAlchemyError, OSError) as exc:
        print(f"Error: {str(exc).splitlines()[0]}")
        return 2
