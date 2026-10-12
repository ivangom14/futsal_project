"""Consulta de actas por API (solo lectura) con datos de prueba controlados: acta fixture 5575697."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from futsal.api.app import create_app
from futsal.importer.report_service import run_report_import
from futsal.importer.service import run_import
from futsal.ingestion.rffm.report_parser import parse_match_report

pytestmark = pytest.mark.integration
FIX = Path(__file__).parents[1] / "fixtures"
URL = "https://www.rffm.es/acta-partido/5575697?temporada=22&competicion=26738243&grupo=26738245"
TABLES = ("matches", "match_observations", "match_reports", "match_report_observations", "match_events",
          "match_officials", "players")


def _import_report(engine: Engine, tmp_path: Path, name: str = "n.json", **game: object) -> None:
    html = (FIX / "rffm_match_report.html").read_text(encoding="utf-8")
    if game:
        data = json.loads(html.split('type="application/json">')[1].split("</script>")[0])
        data["props"]["pageProps"]["game"].update(game)
        html = '<script id="__NEXT_DATA__">' + json.dumps(data) + "</script>"
    path = tmp_path / name
    path.write_text(parse_match_report(html, URL).model_dump_json(), encoding="utf-8")
    assert run_report_import(engine, path).status == "success"


def _one(engine: Engine, sql: str) -> Any:
    with engine.connect() as c:
        return c.scalar(text(sql))


@pytest.fixture()
def api(engine: Engine, tmp_path: Path) -> TestClient:
    assert run_import(engine, FIX / "league_two_rounds.json").status == "success"
    _import_report(engine, tmp_path)
    return TestClient(create_app(engine))


def _ids(engine: Engine) -> dict[str, int]:
    return {
        "group": _one(engine, "SELECT id FROM competition_groups LIMIT 1"),
        "with": _one(engine, "SELECT match_id FROM match_reports LIMIT 1"),
        "no_report_finished": _one(
            engine, "SELECT m.id FROM matches m LEFT JOIN match_reports r ON r.match_id = m.id "
                    "WHERE m.status = 'finished' AND r.id IS NULL ORDER BY m.id LIMIT 1"),
        "scheduled": _one(engine, "SELECT id FROM matches WHERE status = 'scheduled' ORDER BY id LIMIT 1"),
        "home": _one(engine, "SELECT home_team_id FROM matches WHERE id = (SELECT match_id FROM match_reports)"),
        "away": _one(engine, "SELECT away_team_id FROM matches WHERE id = (SELECT match_id FROM match_reports)")}


def test_report_detail_returns_officials_goals_and_consistent_score(api: TestClient, engine: Engine) -> None:
    ids = _ids(engine)
    body = api.get(f"/matches/{ids['with']}/report").json()
    assert body["report_available"] is True and body["report"]["closed"] is True
    assert [(o["name"], o["role"]) for o in body["officials"]] == [
        ("GARCIA LASA, ALVARO", "ARBITRO"), ("GARVALENA CRESPO, MARTA", "ARBITRO"),
        ("ARJONA ESCALONA, MANUEL JOSE", "ARBITRO ASISTENTE")]
    assert [(g["player"], g["minute"]) for g in body["goals"]][:2] == [
        ("GARCIA GUTIERREZ, JAVIER", 7), ("CASTRO CORONADO, SERGIO", 18)]
    assert len(body["goals"]) == 7 and "cards" not in body and "players" not in body
    assert body["consistency"]["events_score"] == [3, 4] == body["consistency"]["report_score"]
    assert body["consistency"]["consistent"] is True and body["consistency"]["differences"] == []
    assert "periodos / resultado por periodos" in body["not_in_source"]


def test_optional_sections_and_validation(api: TestClient, engine: Engine) -> None:
    mid = _ids(engine)["with"]
    body = api.get(f"/matches/{mid}/report", params={"sections": "cards,players,staff"}).json()
    assert [c["player"] for c in body["cards"]] == ["GARCÍA GARCÍA, JAVIER"]
    assert body["cards"][0]["source_code"] and "no está verificado" in body["cards_note"]
    assert len(body["players"]) == 21 and len(body["staff"]) == 3 and "officials" not in body
    assert api.get(f"/matches/{mid}/report", params={"sections": "secret"}).status_code == 400


def test_match_without_report_distinguishes_finished_from_not_played(api: TestClient, engine: Engine) -> None:
    ids = _ids(engine)
    finished = api.get(f"/matches/{ids['no_report_finished']}/report").json()
    assert finished["report_available"] is False and "no implica que no exista" in finished["reason"]
    assert "goals" not in finished
    sched = api.get(f"/matches/{ids['scheduled']}/report").json()
    assert sched["report_available"] is False and "scheduled" in sched["reason"]
    missing = api.get("/matches/999999/report")
    assert missing.status_code == 404 and "no encontrado" in missing.json()["detail"]


def test_scorers_group_goals_by_player_with_partial_coverage(api: TestClient, engine: Engine) -> None:
    g = _ids(engine)["group"]
    body = api.get(f"/groups/{g}/scorers", params={"min_goals": 2}).json()
    assert [(s["player"], s["goals"], s["matches_with_goal"]) for s in body["scorers"]] == [
        ("RODRÍGUEZ OLIVA, IZAN", 2, 1)]
    cov = body["coverage"]
    assert cov["complete"] is False and cov["with_report"] == 1 and cov["without_report_count"] >= 1
    assert "parcial" in cov["note"] and "no prueba" in cov["note"]
    everyone = api.get(f"/groups/{g}/scorers").json()
    assert everyone["total"] == 6 and sum(s["goals"] for s in everyone["scorers"]) == 7
    assert everyone["scorers"][0]["goals"] == 2


def test_events_filters_scope_and_limits(api: TestClient, engine: Engine) -> None:
    ids = _ids(engine)
    g = ids["group"]
    home = api.get(f"/groups/{g}/match-events", params={"team_id": ids["home"]}).json()
    assert {e["team"] for e in home["events"]} == {home["events"][0]["team"]} and home["total"] == 3
    vs = api.get(f"/groups/{g}/match-events", params={"against_team_id": ids["home"]}).json()
    assert vs["total"] == 4 and all(e["opponent"] == home["events"][0]["team"] for e in vs["events"])
    cards = api.get(f"/groups/{g}/match-events", params={"event_type": "card"}).json()
    assert cards["total"] == 1 and "source_code" in cards["events"][0] and "cards_note" in cards
    page = api.get(f"/groups/{g}/match-events", params={"limit": 2}).json()
    assert page["count"] == 2 and page["total"] == 7 and page["truncated"] is True
    for bad in ({"limit": 0}, {"limit": 1000}, {"event_type": "penalty"}):
        assert api.get(f"/groups/{g}/match-events", params=bad).status_code == 400
    assert api.get(f"/groups/{g}/match-events", params={"team_id": 999999}).status_code == 404
    assert api.get(f"/groups/{g}/match-events", params={"round_id": 999999}).status_code == 404
    assert api.get("/groups/999999/scorers").status_code == 404


def test_scope_without_finished_matches_reports_empty_coverage(api: TestClient, engine: Engine) -> None:
    ids = _ids(engine)
    body = api.get(f"/groups/{ids['group']}/scorers", params={"match_id": ids["scheduled"], "min_goals": 2}).json()
    assert body["scorers"] == [] and body["coverage"]["finished_matches"] == 0
    assert "No hay partidos finalizados" in body["coverage"]["note"]


def test_find_players_is_accent_insensitive_and_flags_unknown_names(api: TestClient, engine: Engine) -> None:
    g = _ids(engine)["group"]
    found = api.get(f"/groups/{g}/players", params={"name": "rodriguez oliva"}).json()
    assert [p["name"] for p in found["players"]] == ["RODRÍGUEZ OLIVA, IZAN"] and found["note"] == ""
    pid = found["players"][0]["player_id"]
    own = api.get(f"/groups/{g}/scorers", params={"player_id": pid}).json()
    assert own["scorers"][0]["goals"] == 2
    ghost = api.get(f"/groups/{g}/players", params={"name": "Pimpollo"}).json()
    assert ghost["players"] == [] and ghost["total"] == 0 and "no prueba que no exista" in ghost["note"]
    assert api.get(f"/groups/{g}/players", params={"name": "x"}).status_code == 400
    assert api.get(f"/groups/{g}/scorers", params={"player_id": 999999}).status_code == 404


def test_has_report_flag_and_filter_on_matches(api: TestClient, engine: Engine) -> None:
    g = _ids(engine)["group"]
    rows = api.get(f"/groups/{g}/matches").json()["items"]
    assert sum(r["has_report"] for r in rows) == 1
    missing = api.get(f"/groups/{g}/matches", params={"status": "finished", "has_report": "false"}).json()
    assert missing["count"] >= 1 and not any(r["has_report"] for r in missing["items"])
    assert api.get(f"/groups/{g}/matches", params={"has_report": "true"}).json()["count"] == 1


def test_score_discrepancy_is_exposed_not_silently_overwritten(engine: Engine, tmp_path: Path) -> None:
    assert run_import(engine, FIX / "league_two_rounds.json").status == "success"
    before = _one(engine, "SELECT home_score || '-' || away_score FROM matches WHERE external_id = '5575697'")
    _import_report(engine, tmp_path, goles_local="5")  # acta cerrada con otro marcador: prevalece, con rastro
    client = TestClient(create_app(engine))
    mid = _one(engine, "SELECT id FROM matches WHERE external_id = '5575697'")
    cons = client.get(f"/matches/{mid}/report", params={"sections": "officials"}).json()["consistency"]
    assert cons["competition_score"] == cons["report_score"] == [5, 4]
    assert any(f"{before}" in d and "prevalece" in d for d in cons["differences"])
    assert any("los goles del acta suman 3-4" in d for d in cons["differences"])
    assert cons["consistent"] is False


def test_report_queries_do_not_write(api: TestClient, engine: Engine) -> None:
    ids = _ids(engine)
    g = ids["group"]
    snap = "SELECT " + ", ".join(f"(SELECT count(*) FROM {t})" for t in TABLES)
    with engine.connect() as c:
        before = tuple(c.execute(text(snap)).one())
    for url in (f"/matches/{ids['with']}/report?sections=officials,goals,cards,players,staff",
                f"/groups/{g}/match-events", f"/groups/{g}/scorers", f"/groups/{g}/players?name=garcia",
                f"/groups/{g}/matches?has_report=false", f"/matches/{ids['scheduled']}/report"):
        assert api.get(url).status_code == 200, url
    with engine.connect() as c:
        assert tuple(c.execute(text(snap)).one()) == before
