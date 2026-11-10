"""Informe de descubrimiento v2 (fixtures T22 + muestra de grupos) importado con `import_catalog`: metadatos oficiales."""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from futsal.db.models import Competition, CompetitionGroup
from futsal.importer.catalog_service import import_catalog
from futsal.ingestion.rffm.client import competitions_api_url, groups_url
from futsal.ingestion.rffm.discovery_v2 import discover

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).parents[1] / "fixtures"
LEAGUE = FIXTURES / "league_two_rounds.json"
COMPS = json.loads((FIXTURES / "rffm_catalog" / "api_competitions_s22_t3.json").read_text(encoding="utf-8"))
GROUPS = json.loads((FIXTURES / "rffm_catalog" / "api_groups_sample.json").read_text(encoding="utf-8"))


def fetch(url: str) -> str:
    if url == competitions_api_url("22", "3"):
        return json.dumps(COMPS)
    return next(json.dumps(GROUPS.get(c["codigo"], [])) for c in COMPS if url == groups_url(c["codigo"]))


def with_leagues(report: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    """Como haría `scrape-catalog`: cada grupo descubierto pasa a `downloaded` con su league.json (datos simulados)."""
    for n, g in enumerate(report["groups"]):
        d: dict[str, Any] = json.loads(LEAGUE.read_text(encoding="utf-8"))
        d["competition"].update(external_id=g["competition_id"], name=g["competition_name"])
        d["group"].update(external_id=g["group_id"], name=g["group_name"])
        salt = f"v{n}_"
        for m in d["matches"]:
            m["external_match_id"] = salt + m["external_match_id"]
            m["home_team_external_id"] = salt + m["home_team_external_id"]
            m["away_team_external_id"] = salt + m["away_team_external_id"]
        path = tmp_path / f"{n}.json"
        path.write_text(json.dumps(d), encoding="utf-8")
        g.update(status="downloaded", league_path=str(path))
    return report


def snapshot(engine: Engine) -> dict[str, Any]:
    with Session(engine) as s:
        comps = {c.external_id: (c.category_code, c.category_name, c.format_code, c.active, c.start_date,
                                 c.review_status, c.classification, c.source_meta)
                 for c in s.scalars(select(Competition))}
        groups = {g.external_id: (g.expected_rounds, g.expected_teams, g.delegation, g.has_standings, g.has_scorers)
                  for g in s.scalars(select(CompetitionGroup))}
    return {"comps": comps, "groups": groups}


def test_discovery_report_persists_official_metadata_and_is_idempotent(engine: Engine, tmp_path: Path) -> None:
    report = with_leagues(discover("22", "3", fetch), tmp_path)
    assert len(report["groups"]) == 14
    results = import_catalog(engine, report)
    assert all(r["import_status"] == "success" for r in results), results
    assert all("catalog_meta_error" not in r for r in results)
    first = snapshot(engine)
    alevin = next(v for k, v in first["comps"].items() if k == "26750017")
    assert alevin[0] and alevin[1] and alevin[2] and alevin[3] is True and alevin[5] and alevin[6]  # oficiales + 0007
    assert alevin[7] and "ptos_ganado" in alevin[7]
    assert len(first["groups"]) == 14
    assert all(v == (9, v[1], "CENTRAL R.F.F.M.", True, True) for k, v in first["groups"].items()
               if k in {g["group_id"] for g in report["groups"] if g["competition_id"] == "26750017"})
    assert all(v[1] is not None and v[3] is True for v in first["groups"].values())

    again = import_catalog(engine, report)
    assert all(r["import_status"] in ("success", "unchanged") for r in again), again
    assert all(not r.get("catalog_meta_changes") for r in again)
    assert snapshot(engine) == first
