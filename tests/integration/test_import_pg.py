import json
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from futsal.importer.service import run_import
from futsal.importer.summary import db_summary

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures" / "league_two_rounds.json"


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as c:
        return int(c.scalar(text(f"SELECT count(*) FROM {table}")) or 0)


def _variant(tmp_path: Path, match_id: str, **changes: object) -> Path:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for m in data["matches"]:
        if m["external_match_id"] == match_id:
            m.update(changes)
    p = tmp_path / "changed.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_migration_from_empty(pg_engine: Engine) -> None:
    insp = inspect(pg_engine)
    assert {"seasons", "competitions", "competition_groups", "rounds", "teams", "matches",
            "match_observations", "ingestion_runs", "data_quality_issues"} <= set(
                insp.get_table_names())
    assert insp.get_foreign_keys("matches")
    assert {i["name"] for i in insp.get_indexes("matches")} >= {"ix_matches_status"}


def test_first_import(engine: Engine) -> None:
    r = run_import(engine, FIXTURE)
    assert r.status == "success"
    s = db_summary(engine)
    assert (s["temporadas"], s["competiciones"], s["grupos"], s["jornadas"], s["equipos"],
            s["partidos"], s["observaciones"], s["ejecuciones_importacion"]) == (
                1, 1, 1, 2, 14, 14, 14, 1)
    assert s["partidos_por_estado"] == {"finished": 13, "scheduled": 1}
    with engine.connect() as c:
        run = c.execute(text("SELECT status, matches_processed, observations_created, "
                             "finished_at IS NOT NULL FROM ingestion_runs")).one()
    assert tuple(run) == ("success", 14, 14, True)


def test_second_import_is_idempotent(engine: Engine) -> None:
    run_import(engine, FIXTURE)
    r = run_import(engine, FIXTURE)
    m = r.stats["matches"]
    assert (m["inserted"], m["updated"], m["unchanged"]) == (0, 0, 14)
    assert r.stats["observations"]["inserted"] == 0
    assert (r.total("inserted"), r.total("updated")) == (0, 0)
    s = db_summary(engine)
    assert (s["partidos"], s["equipos"], s["observaciones"], s["ejecuciones_importacion"]) == (
        14, 14, 14, 2)


def test_status_and_score_change_creates_observation(engine: Engine, tmp_path: Path) -> None:
    run_import(engine, FIXTURE)
    changed = _variant(tmp_path, "5575696", status="finished", home_score=2, away_score=1,
                       scheduled_time="18:30:00")
    r = run_import(engine, changed)
    assert r.stats["matches"]["updated"] == 1 and r.stats["matches"]["unchanged"] == 13
    assert r.stats["observations"]["inserted"] == 1
    with engine.connect() as c:
        row = c.execute(text("SELECT status, home_score, away_score FROM matches "
                             "WHERE external_id='5575696'")).one()
        hist = c.execute(text(
            "SELECT o.status FROM match_observations o JOIN matches m ON m.id=o.match_id "
            "WHERE m.external_id='5575696' ORDER BY o.id")).scalars().all()
    assert tuple(row) == ("finished", 2, 1)
    assert hist == ["scheduled", "finished"]
    again = run_import(engine, changed)
    assert again.stats["observations"]["inserted"] == 0
    assert _count(engine, "match_observations") == 15


def test_dry_run_persists_nothing(engine: Engine) -> None:
    r = run_import(engine, FIXTURE, dry_run=True)
    assert r.dry_run and r.stats["matches"]["inserted"] == 14
    assert all(_count(engine, t) == 0 for t in ("seasons", "matches", "ingestion_runs"))


def test_fail_on_quality_issues(engine: Engine, tmp_path: Path) -> None:
    bad = _variant(tmp_path, "5575696", home_score=1, away_score=None)
    r = run_import(engine, bad, fail_on_quality_issues=True)
    assert r.status == "failed" and _count(engine, "matches") == 0
    ok = run_import(engine, bad)
    assert ok.status == "success" and ok.issues == 2  # marcador parcial + conteo de jornada
    assert _count(engine, "data_quality_issues") == 2 and _count(engine, "matches") == 13


def test_constraints(engine: Engine) -> None:
    run_import(engine, FIXTURE)
    stmts = {
        "dup match": "INSERT INTO matches (source, external_id, group_id, round_id, home_team_id, "
                     "away_team_id, timezone, status) SELECT source, external_id, group_id, "
                     "round_id, home_team_id, away_team_id, timezone, status FROM matches LIMIT 1",
        "same team": "UPDATE matches SET away_team_id = home_team_id WHERE id = 1",
        "partial score": "UPDATE matches SET home_score = 1, away_score = NULL WHERE id = 1",
        "dup season": "INSERT INTO seasons (source, external_id) VALUES ('rffm', '22')",
        "dup round": "INSERT INTO rounds (source, external_id, group_id, visible_label) "
                     "SELECT source, external_id, group_id, visible_label FROM rounds LIMIT 1",
        "dup team": "INSERT INTO teams (source, external_id, group_id) "
                    "SELECT source, external_id, group_id FROM teams LIMIT 1",
        "bad status": "UPDATE matches SET status = 'x' WHERE id = 1",
    }
    for label, sql in stmts.items():
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.execute(text(sql))
        assert label


def test_match_round_from_other_group(engine: Engine) -> None:
    run_import(engine, FIXTURE)
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO competition_groups (source, external_id, competition_id) "
            "SELECT 'rffm', 'otro', competition_id FROM competition_groups"))
        c.execute(text("INSERT INTO rounds (source, external_id, group_id, visible_label) "
                       "SELECT 'rffm', 'x', id, 'x' FROM competition_groups "
                       "WHERE external_id = 'otro'"))
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(text("UPDATE matches SET round_id = (SELECT id FROM rounds "
                       "WHERE external_id = 'x') WHERE id = 1"))


def test_rollback_on_error(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from futsal.importer import service
    from futsal.repositories import league

    real = league._observe
    calls = {"n": 0}

    def boom(*a, **k):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 5:
            raise SQLAlchemyError("fallo simulado")
        return real(*a, **k)

    monkeypatch.setattr(league, "_observe", boom)
    r = service.run_import(engine, FIXTURE)
    assert r.status == "failed" and "fallo simulado" in (r.error or "")
    assert _count(engine, "matches") == 0 and _count(engine, "teams") == 0
    assert _count(engine, "match_observations") == 0
    with engine.connect() as c:
        run = c.execute(text("SELECT status, error_summary FROM ingestion_runs")).one()
    assert run[0] == "failed" and "fallo simulado" in run[1]
