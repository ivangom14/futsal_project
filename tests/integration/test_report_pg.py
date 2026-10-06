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


def _codes(engine: Engine) -> list[str]:
    with engine.connect() as c:
        return [r[0] for r in c.execute(text(
            "SELECT code FROM data_quality_issues WHERE entity_type='match_report' ORDER BY id"))]


def _scores(engine: Engine) -> tuple[tuple[int, int], list[tuple[int, int]]]:
    with engine.connect() as c:
        cur = c.execute(text("SELECT home_score, away_score FROM matches "
                             "WHERE external_id='5575697'")).one()
        hist = c.execute(text("SELECT o.home_score, o.away_score FROM match_observations o "
                              "JOIN matches m ON m.id=o.match_id WHERE m.external_id='5575697' "
                              "ORDER BY o.id")).all()
    return (cur[0], cur[1]), [(h[0], h[1]) for h in hist]


def test_closed_report_score_prevails_and_keeps_previous(seeded: Engine, tmp_path: Path) -> None:
    p = _input(tmp_path, goles_local="5")
    r = run_report_import(seeded, p)
    assert r.status == "success" and "report_score_applied" in _codes(seeded)
    assert "report_value_mismatch" not in _codes(seeded)  # el acta ya prevalece: no hay discrepancia
    cur, hist = _scores(seeded)
    assert cur == (5, 4) and hist == [(3, 4), (5, 4)]  # el valor anterior queda en el histórico
    with seeded.connect() as c:
        assert tuple(c.execute(text("SELECT home_score, away_score FROM match_report_observations")).one()) == (5, 4)
    before = _codes(seeded)
    again = run_report_import(seeded, p)  # idempotente: no vuelve a aplicar ni a crear incidencias
    assert again.total("inserted") == 0 and _codes(seeded) == before
    assert _scores(seeded)[1] == [(3, 4), (5, 4)]


def test_stale_league_listing_does_not_revert_report_score(seeded: Engine, tmp_path: Path) -> None:
    run_report_import(seeded, _input(tmp_path, goles_local="5"))
    assert run_import(seeded, FIX / "league_two_rounds.json").status == "success"  # listado con 3-4
    cur, hist = _scores(seeded)
    assert cur == (5, 4) and hist == [(3, 4), (5, 4)]


def test_open_report_does_not_override(seeded: Engine, tmp_path: Path) -> None:
    r = run_report_import(seeded, _input(tmp_path, goles_local="5", acta_cerrada="0"))
    assert r.status == "success" and "report_value_mismatch" in _codes(seeded)
    assert "report_score_applied" not in _codes(seeded)
    assert _scores(seeded) == ((3, 4), [(3, 4)])


def test_own_goal_is_stored_with_type(seeded: Engine, tmp_path: Path) -> None:
    html = (FIX / "rffm_match_report.html").read_text(encoding="utf-8")
    data = json.loads(html.split('type="application/json">')[1].split("</script>")[0])
    g = data["props"]["pageProps"]["game"]
    moved = g["goles_equipo_local"].pop(0)  # un gol del local pasa a propia del visitante (102)
    moved["tipo_gol"] = "102"
    g["goles_equipo_visitante"].append(moved)
    g["goles_local"], g["goles_visitante"] = "3", "4"  # 2 propios + 1 en propia = 3 ; 4
    p = tmp_path / "own.json"
    p.write_text(parse_match_report('<script id="__NEXT_DATA__">' + json.dumps(data) + "</script>",
                                    URL).model_dump_json(), encoding="utf-8")
    assert run_report_import(seeded, p).status == "success"
    with seeded.connect() as c:
        n = c.scalar(text("SELECT count(*) FROM match_events WHERE event_type='own_goal'"))
    assert n == 1


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
