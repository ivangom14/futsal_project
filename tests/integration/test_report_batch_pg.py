from pathlib import Path

import pytest
from sqlalchemy import Engine

from futsal.importer.report_service import report_summary
from futsal.importer.service import run_import
from futsal.ingestion.rffm.client import FetchError
from futsal.report_batch import process_reports

pytestmark = pytest.mark.integration
FIX = Path(__file__).parents[1] / "fixtures"
HTML = (FIX / "rffm_match_report.html").read_text(encoding="utf-8")


@pytest.fixture()
def seeded(engine: Engine) -> Engine:
    assert run_import(engine, FIX / "league_two_rounds.json").status == "success"
    return engine


def test_identity_mismatch_is_isolated_and_never_cached(seeded: Engine, tmp_path: Path) -> None:
    urls: list[str] = []

    def fake(url: str) -> str:  # siempre devuelve el acta 5575697, también para otros partidos
        urls.append(url)
        return HTML

    s = process_reports(seeded, tmp_path, fake, limit=3, requests_made=lambda: len(urls))
    by_id = {m["match_id"]: m for m in s["matches"]}
    assert s["processed"] == 3 and s["imported"] == 1 and s["failed"] == 2
    assert by_id["5575697"]["status"] == "imported" and s["http_requests"] == 3
    failed = [m for m in s["matches"] if m["status"] == "failed"]
    assert all("identidad distinta" in m["error"] for m in failed)
    assert not any((tmp_path / m["match_id"] / "raw.html").exists() for m in failed)
    assert s["database"]["match_reports"] == 1 and s["database"]["players"] == 21


def test_cache_first_and_rerun_has_no_candidates_left(seeded: Engine, tmp_path: Path) -> None:
    (tmp_path / "5575697").mkdir()
    (tmp_path / "5575697" / "raw.html").write_text(HTML, encoding="utf-8")

    def forbidden(url: str) -> str:
        raise AssertionError("no debe descargar si hay snapshot")

    s = process_reports(seeded, tmp_path, forbidden, limit=1)
    assert (s["from_cache"], s["downloaded"], s["imported"], s["http_requests"]) == (1, 0, 1, 0)
    asked: list[str] = []

    def other(url: str) -> str:
        asked.append(url)
        return HTML  # identidad distinta -> falla aislado

    again = process_reports(seeded, tmp_path, other, limit=None)
    assert again["candidates"] == 12 and again["imported"] == 0
    assert not any("/5575697?" in u for u in asked)  # lo ya importado no se pide otra vez
    assert report_summary(seeded)["match_report_observations"] == 1


def test_aborts_after_consecutive_fetch_failures(seeded: Engine, tmp_path: Path) -> None:
    calls: list[str] = []

    def blocked(url: str) -> str:
        calls.append(url)
        raise FetchError("HTTP 403")

    s = process_reports(seeded, tmp_path, blocked, limit=None, requests_made=lambda: len(calls))
    assert s["processed"] == 3 and s["aborted"] and s["failed"] == 3 and len(calls) == 3
