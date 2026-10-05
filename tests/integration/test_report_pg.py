import json
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from futsal.importer.report_service import report_summary, run_report_import
from futsal.importer.service import run_import
from futsal.ingestion.rffm.report_parser import parse_match_report

pytestmark = pytest.mark.integration
FIX = Path(__file__).parents[1] / "fixtures"
URL = "https://www.rffm.es/acta-partido/5575697?temporada=22&competicion=26738243&grupo=26738245"


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as c:
        return int(c.scalar(text(f"SELECT count(*) FROM {table}")) or 0)


def _input(tmp_path: Path, name: str = "n.json", **game_changes: object) -> Path:
    html = (FIX / "rffm_match_report.html").read_text(encoding="utf-8")
    if game_changes:
        data = json.loads(html.split('type="application/json">')[1].split("</script>")[0])
        data["props"]["pageProps"]["game"].update(game_changes)
        html = '<script id="__NEXT_DATA__">' + json.dumps(data) + "</script>"
    p = tmp_path / name
    p.write_text(parse_match_report(html, URL).model_dump_json(), encoding="utf-8")
    return p


@pytest.fixture()
def seeded(engine: Engine) -> Engine:
    assert run_import(engine, FIX / "league_two_rounds.json").status == "success"
    return engine


def test_migration_0002_creates_tables(pg_engine: Engine) -> None:
    names = set(inspect(pg_engine).get_table_names())
    assert {"match_reports", "match_report_observations", "players", "player_team_memberships",
            "match_players", "match_staff", "match_officials", "match_events"} <= names
    assert inspect(pg_engine).get_foreign_keys("match_reports")[0]["referred_table"] == "matches"


def test_first_import_and_relations(seeded: Engine, tmp_path: Path) -> None:
    r = run_report_import(seeded, _input(tmp_path))
    assert r.status == "success" and r.total("inserted") == 79
    s = report_summary(seeded)
    assert (s["match_reports"], s["players"], s["match_players"], s["match_staff"],
            s["match_officials"], s["match_events"], s["quality_issues"]) == (1, 21, 21, 3, 3, 8, 0)
    with seeded.connect() as c:
        linked = c.scalar(text(
            "SELECT count(*) FROM match_players mp JOIN teams t ON t.id = mp.team_id "
            "JOIN match_report_observations o ON o.id = mp.observation_id "
            "JOIN match_reports r ON r.id = o.report_id JOIN matches m ON m.id = r.match_id "
            "WHERE t.id IN (m.home_team_id, m.away_team_id)"))
    assert linked == 21


def test_second_import_is_idempotent(seeded: Engine, tmp_path: Path) -> None:
    p = _input(tmp_path)
    run_report_import(seeded, p)
    before = {t: _count(seeded, t) for t in ("match_reports", "match_report_observations", "players",
                                              "player_team_memberships", "match_players",
                                              "match_staff", "match_officials", "match_events")}
    r = run_report_import(seeded, p)
    assert r.status == "success" and r.total("inserted") == 0 and r.total("updated") == 0
    assert before == {t: _count(seeded, t) for t in before}


def test_unique_constraints(seeded: Engine, tmp_path: Path) -> None:
    run_report_import(seeded, _input(tmp_path))
    with pytest.raises(IntegrityError), seeded.begin() as c:
        c.execute(text("INSERT INTO players (source, external_id, display_name) "
                       "SELECT source, external_id, 'x' FROM players LIMIT 1"))
    with pytest.raises(IntegrityError), seeded.begin() as c:
        c.execute(text("INSERT INTO match_events (observation_id, sequence, event_type, team_id) "
                       "SELECT observation_id, sequence, 'goal', team_id FROM match_events LIMIT 1"))


def test_score_discrepancy_creates_issue_and_keeps_both(seeded: Engine, tmp_path: Path) -> None:
    r = run_report_import(seeded, _input(tmp_path, goles_local="5"))
    assert r.status == "success" and any(c == "report_value_mismatch" for c, _ in r.issues)
    with seeded.connect() as c:
        db_score = c.execute(text("SELECT home_score, away_score FROM matches "
                                  "WHERE external_id = '5575697'")).one()
        obs_score = c.execute(text("SELECT home_score, away_score FROM match_report_observations")).one()
        issue = c.scalar(text("SELECT message FROM data_quality_issues WHERE entity_type='match_report'"))
    assert tuple(db_score) == (3, 4) and tuple(obs_score) == (5, 4)  # no se sobrescribe
    assert "no se consolida" in issue


def test_changed_report_keeps_history(seeded: Engine, tmp_path: Path) -> None:
    run_report_import(seeded, _input(tmp_path, "a.json"))
    r = run_report_import(seeded, _input(tmp_path, "b.json", goles_local="5"))
    assert r.stats["match_report_observations"]["inserted"] == 1
    assert _count(seeded, "match_report_observations") == 2 and _count(seeded, "match_reports") == 1
    assert _count(seeded, "players") == 21  # sin jugadores duplicados


def test_material_error_rolls_back(seeded: Engine, tmp_path: Path) -> None:
    r = run_report_import(seeded, _input(tmp_path, codigo_equipo_local="999"))
    assert r.status == "failed" and "no corresponde" in (r.error or "")
    assert all(_count(seeded, t) == 0 for t in ("match_reports", "match_report_observations",
                                                  "players", "match_players", "match_events"))
    with seeded.connect() as c:
        assert c.scalar(text("SELECT status FROM ingestion_runs WHERE source='rffm-match-report'")) == "failed"
