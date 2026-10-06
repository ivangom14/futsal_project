"""Procesamiento secuencial de actas de partidos finalizados: caché -> parseo -> JSON -> importación."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from futsal.config.targets import RFFM_TARGET
from futsal.importer.report_service import comparison_with_db, report_summary, run_report_import
from futsal.ingestion.rffm.client import FetchError, match_report_url
from futsal.ingestion.rffm.parser import RffmParseError
from futsal.ingestion.rffm.report_parser import parse_match_report
from futsal.repositories.match_reports import Candidate, select_candidates

MAX_CONSECUTIVE_FETCH_FAILURES = 3


def _check_identity(report: Any, cand: Candidate) -> None:
    r = report.report
    got = (r.match_external_id, r.season_external_id, r.competition_external_id, r.group_external_id)
    want = (cand.external_id, cand.season_external_id, cand.competition_external_id,
            cand.group_external_id)
    if got != want:
        raise RffmParseError(f"identidad distinta: acta={got} esperado={want}")


def _process_one(engine: Engine, cand: Candidate, report_dir: Path, fetch: Callable[[str], str],
                 do_import: bool) -> dict[str, Any]:
    url = match_report_url(cand.external_id, cand.season_external_id,
                           cand.competition_external_id, cand.group_external_id)
    out_dir = report_dir / cand.external_id
    raw = out_dir / "raw.html"
    cached = raw.exists()
    html = raw.read_text(encoding="utf-8") if cached else fetch(url)
    report = parse_match_report(html, url)
    _check_identity(report, cand)  # nunca se guarda un snapshot de otra página
    out_dir.mkdir(parents=True, exist_ok=True)
    if not cached:
        raw.write_text(html, encoding="utf-8")
    report.validation.comparison = comparison_with_db(engine, report)
    normalized = out_dir / "normalized.json"
    normalized.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    entry: dict[str, Any] = {
        "match_id": cand.external_id, "round": cand.round_label, "home": cand.home,
        "away": cand.away, "score": cand.score, "source": "cache" if cached else "download",
        "players": len(report.players), "events": len(report.events),
        "discrepancies": [c.field for c in report.validation.comparison or [] if not c.equal],
        "status": "normalized", "error": None}
    if do_import:
        res = run_report_import(engine, normalized)
        entry["status"] = "imported" if res.status == "success" else "failed"
        entry["error"] = res.error
        entry["issues"] = len(res.issues)
        entry["records_inserted"] = res.total("inserted")
    return entry


def process_reports(engine: Engine, report_dir: Path, fetch: Callable[[str], str], *,
                    limit: int | None, do_import: bool = True,
                    requests_made: Callable[[], int] = lambda: 0,
                    now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> dict[str, Any]:
    started = now()
    with Session(engine) as session:
        candidates = select_candidates(session, RFFM_TARGET["group"], limit)
    entries: list[dict[str, Any]] = []
    consecutive, aborted = 0, None
    for cand in candidates:
        try:
            entry = _process_one(engine, cand, report_dir, fetch, do_import)
            consecutive = 0
        except (FetchError, RffmParseError, OSError) as exc:
            entry = {"match_id": cand.external_id, "round": cand.round_label, "status": "failed",
                     "source": None, "error": f"{type(exc).__name__}: {exc}"[:300]}
            consecutive = consecutive + 1 if isinstance(exc, FetchError) else 0
        entries.append(entry)
        if consecutive >= MAX_CONSECUTIVE_FETCH_FAILURES:
            aborted = f"{consecutive} fallos de descarga seguidos; se detiene sin insistir"
            break
    db = report_summary(engine)
    for k in ("first_import", "second_import"):
        db.pop(k, None)
    return {
        "started_at": started.isoformat(), "finished_at": now().isoformat(),
        "candidates": len(candidates), "processed": len(entries),
        "downloaded": sum(1 for e in entries if e["source"] == "download"),
        "from_cache": sum(1 for e in entries if e["source"] == "cache"),
        "imported": sum(e["status"] == "imported" for e in entries), "normalized_only": sum(e["status"] == "normalized" for e in entries),
        "failed": sum(e["status"] == "failed" for e in entries), "http_requests": requests_made(), "aborted": aborted,
        "matches": entries, "database": db}
