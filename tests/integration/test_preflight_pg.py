"""Preflight sobre una BD temporal sembrada con fixtures: misma base, base distinta, API caída, solo lectura, sin secretos."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, text

from futsal.agent.preflight import (
    blocking,
    db_snapshot,
    env_checks,
    render,
    run_preflight,
    verify_same_database,
    write_marker,
)
from futsal.api.app import create_app
from futsal.importer.report_service import run_report_import
from futsal.importer.service import run_import

pytestmark = pytest.mark.integration
FIX = Path(__file__).parents[1] / "fixtures"


@pytest.fixture()
def seeded(engine: Engine) -> Engine:
    assert run_import(engine, FIX / "league_two_rounds.json").status == "success"
    return engine


def test_same_database_is_proven_by_matching_fingerprints(seeded: Engine) -> None:
    same = verify_same_database(seeded, TestClient(create_app(seeded)))
    assert same.verdict == "same" and same.db["fingerprint"] == same.api["fingerprint"]
    assert same.db["matches"] == same.api["matches"] == 14 and same.db["finished"] == 13


class _Tamper(httpx.BaseTransport):
    """API que ve OTROS datos: altera un marcador en la respuesta, como si apuntase a otra base."""

    def __init__(self, inner: TestClient) -> None:
        self.inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        resp = self.inner.request(request.method, str(request.url))
        body: Any = resp.json()
        if request.url.path.endswith("/matches") and body.get("items"):
            body["items"][0]["home_score"] = 99
        return httpx.Response(resp.status_code, json=body)


def test_different_database_is_detected_and_blocks(seeded: Engine) -> None:
    client = httpx.Client(base_url="http://testserver", transport=_Tamper(TestClient(create_app(seeded))))
    same = verify_same_database(seeded, client)
    assert same.verdict == "different" and "NO ven los mismos datos" in same.detail
    checks, same2 = run_preflight(seeded, client, check_docker=False)
    assert blocking(checks, same2) and "BLOQUEADA" in render(checks, same2)


def test_unreachable_api_is_unproven_and_blocks(seeded: Engine) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = httpx.Client(base_url="http://api", transport=httpx.MockTransport(boom))
    checks, same = run_preflight(seeded, client, check_docker=False)
    assert same.verdict == "unproven" and blocking(checks, same)
    assert any(c.name == "API" and c.status == "fail" for c in checks)


def test_preflight_only_reads_and_survives_a_read_only_transaction(seeded: Engine) -> None:
    statements: list[str] = []
    event.listen(seeded, "before_cursor_execute", lambda c, cur, stmt, *a: statements.append(stmt))
    verify_same_database(seeded, TestClient(create_app(seeded)))
    writes = [s for s in statements if s.lstrip().split(None, 1)[0].upper() in
              {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE"}]
    assert statements and not writes
    with seeded.connect().execution_options(postgresql_readonly=True) as c, pytest.raises(Exception):
        c.execute(text("UPDATE matches SET home_score = 0"))  # el modo solo lectura de PostgreSQL lo rechaza


def test_data_audit_counts_reports_gaps_and_discrepancies(seeded: Engine, tmp_path: Path) -> None:
    snap = db_snapshot(seeded)
    assert snap["with_report"] == 0 and snap["finished_without_report"] == 13
    assert snap["score_discrepancies"] == 0 and snap["homonym_names"] == 0
    html = (FIX / "rffm_match_report.html").read_text(encoding="utf-8")
    from futsal.ingestion.rffm.report_parser import parse_match_report

    rep = parse_match_report(html, "https://www.rffm.es/acta-partido/5575697?x=1")
    rep.match.home_score = 9  # acta que contradice la competición (3-4) y NO se corrige: acta abierta
    rep.report.closed = False
    p = tmp_path / "n.json"
    p.write_text(rep.model_dump_json(), encoding="utf-8")
    assert run_report_import(seeded, p).status == "success"
    snap = db_snapshot(seeded)
    assert snap["with_report"] == 1 and snap["finished_without_report"] == 12
    assert snap["reports_not_closed"] == 1 and snap["score_discrepancies"] == 1


def test_marker_and_output_never_contain_secrets(seeded: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-super-secret-key-123456")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://futsal:ContraseñaMuySecreta99@localhost:5432/futsal")
    checks, same = run_preflight(seeded, TestClient(create_app(seeded)), for_live=True, check_docker=False)
    out = render(checks, same)
    marker = tmp_path / "m.json"
    write_marker(checks, same, marker)
    for secret in ("AIza-super-secret-key-123456", "ContraseñaMuySecreta99"):
        assert secret not in out and secret not in marker.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY: definida" in out and "contraseña=definida" in out
    assert not any(c.status == "fail" and c.name == "GEMINI_API_KEY" for c in env_checks(True))
