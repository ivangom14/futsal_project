from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from futsal.api.app import create_app
from futsal.importer.service import run_import

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures" / "league_two_rounds.json"


@pytest.fixture()
def client(engine: Engine) -> Iterator[TestClient]:
    run_import(engine, FIXTURE)
    with TestClient(create_app(engine)) as c:
        yield c


def _group_id(client: TestClient) -> int:
    comp = client.get("/competitions").json()["items"][0]
    return int(client.get(f"/competitions/{comp['id']}/groups").json()["items"][0]["id"])


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "database": "ok"}


def test_competitions_and_groups(client: TestClient) -> None:
    body = client.get("/competitions").json()
    assert body["count"] == 1 == len(body["items"])
    sid = body["items"][0]["season_id"]
    assert client.get(f"/competitions?season_id={sid}").json()["count"] == 1
    assert client.get("/competitions?season_id=999").json()["count"] == 0
    groups = client.get(f"/competitions/{body['items'][0]['id']}/groups").json()
    assert groups["count"] == 1


def test_rounds_teams_matches(client: TestClient) -> None:
    gid = _group_id(client)
    rounds = client.get(f"/groups/{gid}/rounds").json()
    teams = client.get(f"/groups/{gid}/teams").json()
    matches = client.get(f"/groups/{gid}/matches").json()
    assert rounds["count"] >= 2 and teams["count"] >= 2 and matches["count"] >= 2
    assert {"home_team", "away_team", "status", "date"} <= set(matches["items"][0])


def test_match_filters(client: TestClient) -> None:
    gid = _group_id(client)
    allm = client.get(f"/groups/{gid}/matches").json()
    rid = allm["items"][0]["round_id"]
    by_round = client.get(f"/groups/{gid}/matches?round_id={rid}").json()
    assert 0 < by_round["count"] <= allm["count"]
    assert all(m["round_id"] == rid for m in by_round["items"])
    for status in {m["status"] for m in allm["items"]}:
        f = client.get(f"/groups/{gid}/matches?status={status}").json()
        assert f["count"] == sum(m["status"] == status for m in allm["items"])


def test_match_detail(client: TestClient) -> None:
    gid = _group_id(client)
    mid = client.get(f"/groups/{gid}/matches").json()["items"][0]["id"]
    r = client.get(f"/matches/{mid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == mid and body["observations"]


def test_not_found_and_invalid(client: TestClient) -> None:
    assert client.get("/matches/999999").status_code == 404
    assert client.get("/competitions/999999/groups").status_code == 404
    for sub in ("rounds", "teams", "matches"):
        assert client.get(f"/groups/999999/{sub}").status_code == 404
    gid = _group_id(client)
    assert client.get(f"/groups/{gid}/matches?status=bogus").status_code == 400
    assert client.get(f"/groups/{gid}/matches?round_id=abc").status_code == 400
    assert client.get("/matches/abc").status_code == 400


def test_matches_filter_by_team_and_unknown_team(client: TestClient) -> None:
    gid = _group_id(client)
    teams = client.get(f"/groups/{gid}/teams").json()["items"]
    allm = client.get(f"/groups/{gid}/matches").json()["items"]
    tid = teams[0]["id"]
    mine = client.get(f"/groups/{gid}/matches?team_id={tid}").json()
    assert mine["count"] >= 1
    assert all(tid in (m["home_team_id"], m["away_team_id"]) for m in mine["items"])
    assert mine["count"] == sum(tid in (m["home_team_id"], m["away_team_id"]) for m in allm)
    both = client.get(f"/groups/{gid}/matches?team_id={tid}&status=finished").json()
    assert all(m["status"] == "finished" for m in both["items"])
    r = client.get(f"/groups/{gid}/matches?team_id=999999")
    assert r.status_code == 404 and "equipo" in r.json()["detail"]


def test_match_date_falls_back_to_scheduled_date_when_no_time(client: TestClient, engine: Engine) -> None:
    gid = _group_id(client)
    with engine.begin() as c:  # BD temporal de test: un partido sin hora publicada
        c.execute(text("UPDATE matches SET scheduled_at = NULL, scheduled_time = NULL "
                       "WHERE id = (SELECT min(id) FROM matches)"))
    items = client.get(f"/groups/{gid}/matches").json()["items"]
    first = min(items, key=lambda m: m["id"])
    assert first["date"] is not None and len(first["date"]) == 10  # AAAA-MM-DD, no null
    detail = client.get(f"/matches/{first['id']}").json()
    assert all(o["date"] for o in detail["observations"])
