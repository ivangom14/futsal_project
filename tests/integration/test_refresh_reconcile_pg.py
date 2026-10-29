"""Fase 21: reconciliación de SOLO LECTURA (categorías, sin escrituras) y frescura expuesta por API y MCP."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session as connect
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from futsal.api.app import create_app
from futsal.db.table_models import ScorerSnapshot, StandingsSnapshot
from futsal.importer.service import run_import
from futsal.mcp_server.server import create_server
from futsal.refresh.freshness import StalePolicy, group_freshness
from futsal.refresh.reconcile import reconcile
from futsal.refresh.service import RefreshOptions, run_refresh
from helpers_tables import FakeSite

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures" / "league_two_rounds.json"
COMP, GROUP = "26738243", "26738245"
NOW = datetime(2099, 1, 1, 12, 0, tzinfo=UTC)
TABLES = ("matches", "match_observations", "teams", "rounds", "competition_groups", "ingestion_runs", "data_quality_issues",
          "dataset_refreshes", "standings_snapshots", "scorer_snapshots", "match_reports")


def _league() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def _file(tmp_path: Path, data: dict[str, Any], name: str = "src.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _fingerprint(engine: Engine) -> dict[str, tuple[int, str]]:
    with engine.connect() as c:
        return {t: (int(c.scalar(text(f"SELECT count(*) FROM {t}")) or 0),
                    str(c.scalar(text(f"SELECT md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), '')) FROM {t} x")))) for t in TABLES}


def _kinds(rep: dict[str, Any]) -> set[str]:
    return set(rep["groups"][0]["kinds"])


@pytest.fixture()
def seeded(engine: Engine) -> Engine:
    assert run_import(engine, FIXTURE).status == "success"
    return engine


def test_categories_and_nothing_is_written(seeded: Engine, tmp_path: Path) -> None:
    src = _league()
    src["matches"] = [m for m in src["matches"] if m["external_match_id"] != "5575696"]  # ausente de una jornada completa
    for m in src["matches"]:
        if m["external_match_id"] == "5575697":
            m.update(status="scheduled", home_score=None, away_score=None)  # regresión de un finalizado
        if m["external_match_id"] == "5575698":
            m["scheduled_date"] = "2027-03-03"  # cambio de fecha confirmado por la fuente
    new = dict(src["matches"][0], external_match_id="9999999")  # partido nuevo
    src["matches"].append(new)
    before = _fingerprint(seeded)
    rep = reconcile(seeded, [(COMP, GROUP, _file(tmp_path, src))])
    assert _fingerprint(seeded) == before  # ni escrituras ni borrados
    assert rep["read_only"] is True
    kinds = _kinds(rep)
    assert {"historical:absent_from_source", "possible_inconsistency:finished_regression", "confirmed_difference:scheduled_date_differs",
            "confirmed_difference:missing_in_db"} <= kinds
    assert "temporary_absence:report_pending" in kinds  # partidos finalizados sin acta
    assert rep["totals"]["historical"] == 1


def test_incomplete_source_is_temporary_or_ingest_error_never_a_difference(seeded: Engine, tmp_path: Path) -> None:
    src = _league()
    src["rounds"][1]["status"] = "failed"  # la jornada 2 no se descargó
    src["matches"] = [m for m in src["matches"] if m["external_round_id"] == "1"]
    rep = reconcile(seeded, [(COMP, GROUP, _file(tmp_path, src))])
    kinds = _kinds(rep)
    assert "ingest_error:round_not_ok" in kinds
    assert "temporary_absence:absent_round_not_downloaded" in kinds
    assert not any(k.startswith("confirmed_difference") for k in kinds) and "historical:absent_from_source" not in kinds


def test_identity_group_missing_and_no_snapshot_cases(seeded: Engine, tmp_path: Path) -> None:
    wrong = _league()
    wrong["group"] = {"external_id": "777"}
    rep = reconcile(seeded, [(COMP, GROUP, _file(tmp_path, wrong)), (COMP, "888", None), (COMP, GROUP, tmp_path / "no-existe.json")])
    assert "ingest_error:identity_mismatch" in set(rep["groups"][0]["kinds"])
    assert "confirmed_difference:group_not_in_db" in set(rep["groups"][1]["kinds"])
    assert "temporary_absence:no_source_snapshot" in set(rep["groups"][2]["kinds"])


def test_match_of_another_group_is_a_possible_inconsistency(seeded: Engine, tmp_path: Path) -> None:
    other = _league()
    other["group"] = {"external_id": "26738244"}
    run_import(seeded, _file(tmp_path, dict(other, matches=[], rounds=other["rounds"])))  # crea el otro grupo vacío
    rep = reconcile(seeded, [(COMP, "26738244", _file(tmp_path, other, "o.json"))])
    assert "possible_inconsistency:identity_conflict" in _kinds(rep)


def test_tables_outdated_and_partial_last_attempt_are_reported(seeded: Engine) -> None:
    with Session(seeded) as s, s.begin():
        gid = int(s.scalar(text("SELECT id FROM competition_groups")) or 0)
        t0 = NOW - timedelta(days=30)
        s.add(StandingsSnapshot(group_id=gid, round_number=1, current_round=2, status="ok", content_hash="a", row_count=0, source_url="u",
                                fetched_at=t0, first_seen_at=t0, last_seen_at=t0))
        s.add(ScorerSnapshot(group_id=gid, status="ok", content_hash="a", row_count=0, goals_total=0, source_url="u", fetched_at=t0,
                             first_seen_at=t0, last_seen_at=t0))
        s.add(ScorerSnapshot(group_id=gid, status="incomplete", content_hash="b", row_count=0, goals_total=0, source_url="u",
                             fetched_at=NOW, first_seen_at=NOW, last_seen_at=NOW))
    rep = reconcile(seeded, [(COMP, GROUP, None)])
    kinds = _kinds(rep)
    assert "possible_inconsistency:standings_behind_current_round" in kinds
    assert "temporary_absence:scorers_latest_attempt_incomplete" in kinds


def test_database_itself_blocks_writes_during_reconciliation(seeded: Engine) -> None:
    with Session(seeded) as s:
        s.execute(text("SET TRANSACTION READ ONLY"))
        with pytest.raises(DBAPIError, match="read-only"):
            s.execute(text("UPDATE matches SET venue='x'"))


# --------------------------------------------------------------------------------------------- frescura (API y MCP)
def _refreshed(engine: Engine, tmp_path: Path) -> int:
    work = tmp_path / "work" / f"c{COMP}" / f"g{GROUP}"
    work.mkdir(parents=True)
    (work / "league.json").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    inv = {"season": {"external_id": "22"}, "game_type": "3", "entries": [{
        "identification": "confirmed", "groups_status": "ok", "competition": {"external_id": COMP, "name": "C"},
        "groups": [{"external_id": GROUP, "name": "G", "expected_rounds": 2, "expected_teams": 14}]}]}
    out = run_refresh(engine, inv, tmp_path / "work", tmp_path / "t", tmp_path / "r", FakeSite({}),
                      RefreshOptions(datasets=("matches",), offline=True), now=lambda: NOW)
    assert out["status"] == "success"
    with Session(engine) as s:
        return int(s.scalar(text("SELECT id FROM competition_groups")) or 0)


def test_freshness_distinguishes_each_dataset_and_flags_stale_never_and_partial(engine: Engine, tmp_path: Path) -> None:
    gid = _refreshed(engine, tmp_path)
    with Session(engine) as s:
        fresh = group_freshness(s, gid, now=NOW + timedelta(hours=1))
        stale = group_freshness(s, gid, now=NOW + timedelta(hours=100))
    assert fresh is not None and stale is not None
    m = fresh["datasets"]["matches"]
    assert m["status"] == "fresh" and m["basis"] == "refresh_log" and m["last_valid_at"] == NOW.isoformat() and m["last_attempt"]["status"] == "success"
    assert fresh["datasets"]["standings"]["status"] == "never" and fresh["datasets"]["scorers"]["status"] == "never"
    assert fresh["datasets"]["reports"]["detail"]["pending"] == 13
    assert stale["datasets"]["matches"]["status"] == "stale" and any("hace 100" in w for w in stale["warnings"])
    assert fresh["last_run"]["status"] == "success"
    with Session(engine) as s:
        wide = group_freshness(s, gid, now=NOW + timedelta(hours=100), policy=StalePolicy(matches=500))
    assert wide is not None and wide["datasets"]["matches"]["status"] == "fresh"
    # un intento posterior parcial no adelanta la última actualización válida y se avisa
    from futsal.refresh.freshness import record_dataset

    with Session(engine) as s, s.begin():
        record_dataset(s, gid, "matches", "partial", run_id=None, message="descarga incompleta", source_fetched_at=None,
                       now=NOW + timedelta(hours=2))
    with Session(engine) as s:
        after = group_freshness(s, gid, now=NOW + timedelta(hours=3))
    assert after is not None and after["datasets"]["matches"]["last_valid_at"] == NOW.isoformat()
    assert after["datasets"]["matches"]["last_attempt"]["status"] == "partial"
    assert any("fue partial" in w and "descarga incompleta" in w for w in after["warnings"])


def test_api_and_mcp_expose_freshness_read_only(engine: Engine, tmp_path: Path) -> None:
    gid = _refreshed(engine, tmp_path)
    client = TestClient(create_app(engine))
    body = client.get(f"/groups/{gid}/freshness").json()
    assert set(body["datasets"]) == {"matches", "reports", "standings", "scorers"} and "note" in body
    with Session(engine) as s:
        cid = int(s.scalar(text("SELECT competition_id FROM competition_groups")) or 0)
    comp = client.get(f"/competitions/{cid}/freshness").json()
    assert comp["groups"][0]["datasets"]["matches"]["status"] in ("fresh", "stale")
    assert client.get("/groups/99999/freshness").status_code == 404 and client.get("/competitions/99999/freshness").status_code == 404
    for verb in (client.post, client.put, client.delete, client.patch):
        assert verb(f"/groups/{gid}/freshness").status_code == 405  # nada que escriba
    server = create_server(client)

    async def go() -> tuple[list[str], dict[str, Any], dict[str, Any], str]:
        async with connect(server._mcp_server) as session:
            names = [t.name for t in (await session.list_tools()).tools]
            by_group = await session.call_tool("get_data_freshness", {"group_id": gid})
            by_comp = await session.call_tool("get_data_freshness", {"competition_id": cid})
            none = await session.call_tool("get_data_freshness", {})
            return names, json.loads(by_group.content[0].text), json.loads(by_comp.content[0].text), str(none.content[0].text)  # type: ignore[union-attr]

    names, group, competition, error = asyncio.run(go())
    assert group["datasets"]["matches"]["last_valid_at"] == NOW.isoformat() and competition["competition_id"] == cid
    assert "error_validacion" in error
    forbidden = ("refresh", "update", "ingest", "scrape", "import", "recover", "write")
    assert not [n for n in names if any(f in n for f in forbidden)]  # el LLM no puede lanzar ni escribir actualizaciones
