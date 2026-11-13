"""Escudos de punta a punta con PostgreSQL: importación → crest-backfill (sin red) → API `/crests`."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from futsal.api.app import create_app
from futsal.importer.crest_backfill import run_crest_backfill
from futsal.importer.service import run_import

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_backfill_idempotente_y_api(engine: Engine, tmp_path: Path) -> None:
    run_import(engine, FIXTURES / "league_two_rounds.json")
    shutil.copy(FIXTURES / "rffm_round_2.html", tmp_path / "jornada.html")

    dry = run_crest_backfill(engine, tmp_path, dry_run=True)
    assert dry["teams_updated"] > 0
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM teams WHERE crest_url IS NOT NULL")).scalar() == 0  # dry-run no guarda

    first = run_crest_backfill(engine, tmp_path)
    assert first["teams_updated"] == dry["teams_updated"] and first["codes"] == 14
    again = run_crest_backfill(engine, tmp_path)
    assert again["teams_updated"] == 0 and again["teams_unchanged"] == first["teams_updated"]

    with TestClient(create_app(engine)) as client:
        body = client.get("/crests").json()
    assert body["count"] == first["teams_updated"]
    for row in body["items"]:
        assert row["crest_url"].startswith("https://appweb.rffm.es/pnfg/pimg/Clubes/") and row["name"]
