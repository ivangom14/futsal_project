"""Fase 16: ingesta, API, MCP y agente de las clasificaciones y goleadores oficiales (PostgreSQL temporal; sin red ni LLM real)."""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session as connect
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from futsal.agent.agent import Agent
from futsal.agent.llm import LLMResponse, ToolCall
from futsal.agent.mcp_client import SessionMcpClient
from futsal.api.app import create_app
from futsal.db.models import Competition, CompetitionGroup, Player, Season, Team
from futsal.db.table_models import (
    ScorerSnapshot,
    StandingsRow,
    StandingsSnapshot,
    TableIngestAttempt,
)
from futsal.importer.table_service import coverage, ingest_tables
from futsal.ingestion.rffm.client import FetchError
from futsal.mcp_server.server import create_server
from helpers_tables import COMP, GROUP, FakeSite, inventory, load

pytestmark = pytest.mark.integration
NOW = datetime(2099, 10, 20, 12, 0, tzinfo=UTC)
CUP_GROUP, UNKNOWN_GROUP = "90000004", "90000099"
TEAM_CODES = [f"9100000{i}" for i in range(1, 7)]


def _seed(engine: Engine) -> dict[str, int]:
    """Dos temporadas con el MISMO código de equipo en grupos distintos, una copa y un jugador conocido por las actas."""
    ids: dict[str, int] = {}
    with Session(engine) as s, s.begin():
        for sid, name in (("99", "2099-2100"), ("98", "2098-2099")):
            season = Season(external_id=sid, name=name)
            s.add(season)
            s.flush()
            comp = Competition(external_id=COMP if sid == "99" else "90000010", season_id=season.id, name="LIGA EJEMPLO FS")
            s.add(comp)
            s.flush()
            group = CompetitionGroup(external_id=GROUP if sid == "99" else "90000011", competition_id=comp.id, name="Grupo 1")
            s.add(group)
            s.flush()
            ids[f"group{sid}"], ids[f"comp{sid}"] = group.id, comp.id
            s.add_all(Team(external_id=code, group_id=group.id, name=f"EQUIPO {code}") for code in TEAM_CODES)
        cup = CompetitionGroup(external_id=CUP_GROUP, competition_id=ids["comp99"], name="Eliminatorias")
        s.add(cup)
        s.add(Player(external_id="7000001", display_name="GARCIA LOPEZ, ANA"))
        s.flush()
        ids["cup"] = cup.id
    return ids


@pytest.fixture()
def db(engine: Engine) -> Engine:
    _seed(engine)
    return engine


def _inv(extra: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    groups = [{"external_id": GROUP, "name": "Grupo 1", "expected_rounds": 10, "expected_teams": 6}] + (extra or [])
    return inventory(groups)


def _site() -> FakeSite:
    return FakeSite({("clasificaciones", GROUP, None): load("standings_round4_full.html"),
                     ("clasificaciones", GROUP, "1"): load("standings_round1.html"),
                     ("goleadores", GROUP, None): load("scorers_full.html")})


def _run(engine: Engine, kind: str, site: FakeSite, tmp_path: Path, inv: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    kw.setdefault("now", lambda: NOW)
    kw.setdefault("sleep", lambda _s: None)
    return ingest_tables(engine, kind, inv or _inv(), tmp_path, site, **kw)


def _count(engine: Engine, model: Any) -> int:
    with Session(engine) as s:
        return int(s.scalar(select(func.count()).select_from(model)) or 0)


# ------------------------------------------------------------------------------------------------ ingesta
def test_standings_are_persisted_linked_and_idempotent(db: Engine, tmp_path: Path) -> None:
    first = _run(db, "standings", _site(), tmp_path)
    assert first["groups"][0]["status"] == "ok" and first["groups"][0]["rounds"][0]["change"] == "new"
    assert (_count(db, StandingsSnapshot), _count(db, StandingsRow)) == (1, 6)
    again = _run(db, "standings", _site(), tmp_path, refresh=True)
    assert again["groups"][0]["rounds"][0]["change"] == "unchanged" and (_count(db, StandingsSnapshot), _count(db, StandingsRow)) == (1, 6)
    with Session(db) as s:
        rows = s.scalars(select(StandingsRow).order_by(StandingsRow.position)).all()
        assert all(r.team_id is not None for r in rows) and rows[0].team_external_id == "91000001" and rows[0].points == 10
        assert s.scalar(select(func.count()).select_from(TableIngestAttempt)) == 2


def test_a_change_in_the_published_table_creates_a_new_capture_and_keeps_the_old_one(db: Engine, tmp_path: Path) -> None:
    _run(db, "standings", _site(), tmp_path)
    changed = load("standings_round4_full.html").replace('\\"puntos\\": \\"10\\"', '\\"puntos\\": \\"11\\"').replace('"puntos": "10"', '"puntos": "11"')
    assert changed != load("standings_round4_full.html")
    rep = _run(db, "standings", FakeSite({("clasificaciones", GROUP, None): changed}), tmp_path, refresh=True)
    assert rep["groups"][0]["rounds"][0]["change"] == "new" and _count(db, StandingsSnapshot) == 2
    body = TestClient(create_app(db)).get(f"/groups/{_gid(db)}/standings").json()
    assert body["standings"][0]["points"] == 11                                                    # la captura más reciente


def test_rounds_are_separate_snapshots_and_the_history_is_queryable(db: Engine, tmp_path: Path) -> None:
    _run(db, "standings", _site(), tmp_path)
    _run(db, "standings", _site(), tmp_path, round_number=1)
    client, gid = TestClient(create_app(db)), _gid(db)
    assert client.get(f"/groups/{gid}/standings").json()["source"]["round"] == 4
    one = client.get(f"/groups/{gid}/standings?round=1").json()
    assert one["source"]["round"] == 1 and one["standings"][0]["team"] == "A.D. EJEMPLO TRES" and one["source"]["rounds_available"] == [1, 4]
    missing = client.get(f"/groups/{gid}/standings?round=3").json()
    assert missing["status"] == "round_not_ingested" and missing["available_rounds"] == [1, 4] and "standings" not in missing


def test_unavailable_round_is_recorded_and_stores_nothing_as_that_round(db: Engine, tmp_path: Path) -> None:
    site = FakeSite({("clasificaciones", GROUP, "9"): load("standings_round4_full.html")})
    rep = _run(db, "standings", site, tmp_path, round_number=9)
    assert rep["groups"][0]["status"] == "round_unavailable" and _count(db, StandingsSnapshot) == 0
    with Session(db) as s:
        a = s.scalars(select(TableIngestAttempt)).one()
        assert (a.outcome, a.requested_round, a.error_code) == ("round_unavailable", 9, "round_unavailable")


def test_failures_are_recorded_and_one_table_does_not_block_the_other(db: Engine, tmp_path: Path) -> None:
    broken = FakeSite({("clasificaciones", GROUP, None): load("standings_round4_full.html"), ("goleadores", GROUP, None): load("scorers_null.html")})
    st = _run(db, "standings", broken, tmp_path)
    sc = _run(db, "scorers", broken, tmp_path)
    assert st["groups"][0]["status"] == "ok" and sc["groups"][0]["status"] == "failed" and sc["groups"][0]["rounds"][0]["error_code"] == "transient"
    assert _count(db, ScorerSnapshot) == 0 and _count(db, StandingsSnapshot) == 1
    cov = coverage(db, _inv())["detail"][0]
    assert cov["standings"]["status"] == "ok" and cov["scorers"]["status"] == "failed" and cov["scorers"]["last_attempt"]["error_code"] == "transient"
    gid = _gid(db)
    api = TestClient(create_app(db)).get(f"/groups/{gid}/scorer-table").json()
    assert api["status"] == "failed" and "scorers" not in api and "no constan" in api["message"]       # no se devuelve una tabla vacía


def test_a_failed_refresh_keeps_serving_the_last_good_capture_with_a_warning(db: Engine, tmp_path: Path) -> None:
    _run(db, "scorers", _site(), tmp_path)
    bad = FakeSite({("goleadores", GROUP, None): FetchError("HTTP 500")})
    later = datetime(2099, 10, 21, tzinfo=UTC)
    _run(db, "scorers", bad, tmp_path, refresh=True, now=lambda: later)
    body = TestClient(create_app(db)).get(f"/groups/{_gid(db)}/scorer-table").json()
    assert body["status"] == "ok" and len(body["scorers"]) == 6 and "falló" in body["warning"] and body["last_attempt"]["outcome"] == "failed"


def test_resume_skips_done_work_and_retries_failures(db: Engine, tmp_path: Path) -> None:
    site = _site()
    _run(db, "standings", site, tmp_path)
    skipped = _run(db, "standings", site, tmp_path, resume=True)
    assert skipped["groups"][0]["status"] == "skipped" and site.requests == 1                       # ni red ni nuevo intento
    old = _run(db, "standings", site, tmp_path, resume=True, now=lambda: NOW + timedelta(hours=13), refresh=False)
    assert old["groups"][0]["status"] == "ok"                                                      # pasada la ventana se vuelve a procesar
    flaky = FakeSite({("goleadores", GROUP, None): [FetchError("HTTP 500")]})
    assert _run(db, "scorers", flaky, tmp_path, resume=True)["groups"][0]["status"] == "failed"
    retried = _run(db, "scorers", _site(), tmp_path, resume=True)
    assert retried["groups"][0]["status"] == "ok" and _count(db, ScorerSnapshot) == 1                # el fallo sí se reintenta


def test_cup_groups_and_groups_missing_in_the_database_are_reported_not_hidden(db: Engine, tmp_path: Path) -> None:
    extra = [{"external_id": CUP_GROUP, "name": "Eliminatorias", "expected_rounds": 3, "expected_teams": 8},
             {"external_id": UNKNOWN_GROUP, "name": "Grupo X", "expected_rounds": 1, "expected_teams": 2}]
    site = FakeSite({("clasificaciones", GROUP, None): load("standings_round4_full.html"),
                     ("clasificaciones", CUP_GROUP, None): load("standings_cup.html").replace(GROUP, CUP_GROUP)})
    rep = _run(db, "standings", site, tmp_path, _inv(extra))
    by = {g["group_id"]: g["status"] for g in rep["groups"]}
    assert by == {GROUP: "ok", CUP_GROUP: "not_applicable", UNKNOWN_GROUP: "no_group_in_db"}
    cup = TestClient(create_app(db)).get(f"/groups/{_seed_ids(db)['cup']}/standings").json()
    assert cup["status"] == "not_applicable" and "standings" not in cup
    cov = coverage(db, _inv(extra))
    assert cov["groups"] == 3 and cov["by_status"]["standings"] == {"ok": 1, "not_applicable": 1, "no_group_in_db": 1}


def test_incomplete_and_empty_tables_keep_their_status(db: Engine, tmp_path: Path) -> None:
    inc = _run(db, "standings", FakeSite({("clasificaciones", GROUP, None): load("standings_incomplete_row.html")}), tmp_path)
    assert inc["groups"][0]["status"] == "incomplete"
    api = TestClient(create_app(db)).get(f"/groups/{_gid(db)}/standings").json()
    assert api["status"] == "incomplete" and api["coverage"]["complete"] is False and api["coverage"]["warnings"]
    empty = _run(db, "scorers", FakeSite({("goleadores", GROUP, None): load("scorers_empty.html")}), tmp_path)
    assert empty["groups"][0]["status"] == "empty"
    body = TestClient(create_app(db)).get(f"/groups/{_gid(db)}/scorer-table").json()
    assert body["status"] == "empty" and body["scorers"] == [] and body["coverage"]["complete"] is True   # vacía VÁLIDA, distinta de una caída


# ------------------------------------------------------------------------------------------------ API
def _gid(engine: Engine) -> int:
    return _seed_ids(engine)["group99"]


def _seed_ids(engine: Engine) -> dict[str, int]:
    with Session(engine) as s:
        groups = {g.external_id: g.id for g in s.scalars(select(CompetitionGroup))}
        return {"group99": groups[GROUP], "group98": groups["90000011"], "cup": groups[CUP_GROUP]}


def test_api_distinguishes_not_ingested_unknown_group_and_invalid_parameters(db: Engine) -> None:
    c, gid = TestClient(create_app(db)), _gid(db)
    for path in ("standings", "scorer-table"):
        body = c.get(f"/groups/{gid}/{path}").json()
        assert body["status"] == "not_ingested" and "no los reconstruyas" in body["message"] and "standings" not in body and "scorers" not in body
        assert c.get(f"/groups/99999/{path}").status_code == 404
    assert c.get(f"/groups/{gid}/standings?round=0").status_code == 400
    assert c.get(f"/groups/{gid}/scorer-table?limit=0").status_code == 400
    assert c.get(f"/groups/{gid}/scorer-table?name=a").status_code == 400
    assert c.get("/competitions/99999/scorer-table").status_code == 404
    assert c.get(f"/groups/{gid}/scorer-table?name=%27%3B%20DROP%20TABLE%20scorer_rows%3B--").status_code == 200


def test_standings_keep_the_published_order_and_derive_only_goal_difference(db: Engine, tmp_path: Path) -> None:
    _run(db, "standings", _site(), tmp_path)
    body = TestClient(create_app(db)).get(f"/groups/{_gid(db)}/standings").json()
    assert body["status"] == "ok" and body["derived"] == ["goal_difference"] and body["coverage"]["played_min"] == 3
    assert [r["position"] for r in body["standings"]] == [1, 2, 3, 4, 5, 6]
    first, second = body["standings"][0], body["standings"][1]
    assert first["team"] == "C.D. EJEMPLO UNO" and first["goal_difference"] == 8 and first["zone"] == "ASCENSO"
    assert second["played"] == 3 and second["points"] == 9 and "penalty_points" not in second                 # 9 pts con un partido menos, tal cual
    assert body["source"]["round"] == 4 and body["source"]["round_date"] == "2099-10-17" and body["source"]["current_round"] == 4
    assert "null" not in json.dumps(body) and "OFICIAL" in body["note"]


def test_scorer_table_ties_same_names_name_search_and_team_filter(db: Engine, tmp_path: Path) -> None:
    _run(db, "scorers", _site(), tmp_path)
    c, gid = TestClient(create_app(db)), _gid(db)
    top = c.get(f"/groups/{gid}/scorer-table?limit=4").json()
    assert top["matching"] == 6 and top["shown"] == 4 and top["coverage"]["players_listed"] == 6 and top["coverage"]["goals_total"] == 21
    assert [s["rank"] for s in top["scorers"]] == [1, 2, 2, 2] and top["scorers"][1]["tied_with"] == 2 and "tied_with" not in top["scorers"][0]
    assert top["derived"] == ["rank", "tied_with"] and "solo figuran jugadores con al menos un gol" in top["note"]
    ana = c.get(f"/groups/{gid}/scorer-table?name=garcía lópez").json()                             # sin acentos y sin importar mayúsculas
    assert ana["matching"] == 2 and {s["team"] for s in ana["scorers"]} == {"C.D. EJEMPLO UNO", "C.D.E. EJEMPLO CUATRO"}
    assert sorted(s["goals"] for s in ana["scorers"]) == [4, 6]
    known = next(s for s in ana["scorers"] if s["goals"] == 6)
    assert known.get("player_id") and "player_id" not in next(s for s in ana["scorers"] if s["goals"] == 4)  # solo el jugador conocido por actas
    none = c.get(f"/groups/{gid}/scorer-table?name=inexistente").json()
    assert none["scorers"] == [] and none["status"] == "ok" and "no consta" in none["message"]
    team = c.get(f"/groups/{gid}/scorer-table?team_id={known['team_id']}").json()
    assert team["matching"] == 2 and all(s["team_id"] == known["team_id"] for s in team["scorers"])


def test_seasons_and_competitions_are_kept_apart_and_competition_view_lists_every_group(db: Engine, tmp_path: Path) -> None:
    _run(db, "scorers", _site(), tmp_path)
    ids = _seed_ids(db)
    c = TestClient(create_app(db))
    assert c.get(f"/groups/{ids['group98']}/scorer-table").json()["status"] == "not_ingested"     # otra temporada: sin mezcla
    assert c.get(f"/groups/{ids['group98']}/standings").json()["status"] == "not_ingested"
    with Session(db) as s:
        comp_id = s.scalar(select(CompetitionGroup.competition_id).where(CompetitionGroup.id == ids["group99"]))
    comp = c.get(f"/competitions/{comp_id}/scorer-table").json()
    by_group = {g["group"]: g for g in comp["groups"]}
    assert set(by_group) == {"Grupo 1", "Eliminatorias"} and by_group["Grupo 1"]["scorers"][0]["goals"] == 6
    assert by_group["Eliminatorias"]["status"] == "not_ingested"
    cov = c.get("/tables/coverage").json()
    assert cov["groups"] == 3 and cov["by_status"]["scorers"] == {"ok": 1, "not_ingested": 2}


# ------------------------------------------------------------------------------------------------ MCP y agente
def test_mcp_tools_go_through_the_api_and_old_tools_keep_working(db: Engine, tmp_path: Path) -> None:
    _run(db, "standings", _site(), tmp_path)
    _run(db, "scorers", _site(), tmp_path)
    gid = _gid(db)
    server = create_server(TestClient(create_app(db)))

    async def go() -> list[tuple[bool, dict[str, Any] | str]]:
        out: list[tuple[bool, dict[str, Any] | str]] = []
        async with connect(server._mcp_server) as session:
            for name, args in (("get_standings", {"group_id": gid}), ("get_standings", {"group_id": gid, "round": 2}),
                               ("get_scorer_table", {"group_id": gid, "limit": 2}), ("get_scorer_table", {"group_id": gid, "name": "nerin"}),
                               ("get_scorer_table", {}), ("get_standings", {"group_id": 99999}), ("list_competitions", {}),
                               ("top_scorers", {"group_id": gid}), ("search_rules", {"query": "saque de esquina"})):
                res = await session.call_tool(name, args)
                text_ = res.content[0].text  # type: ignore[union-attr]
                out.append((res.isError, text_ if res.isError else json.loads(text_)))
        return out

    standings, other_round, top2, nobody, no_ids, missing_group, comps, actas, rules = asyncio.run(go())
    assert not standings[0] and standings[1]["standings"][0]["team"] == "C.D. EJEMPLO UNO"
    assert other_round[1]["status"] == "round_not_ingested"
    assert [s["goals"] for s in top2[1]["scorers"]] == [6, 4] and nobody[1]["scorers"] == []
    assert no_ids[0] and "group_id o competition_id" in str(no_ids[1])
    assert missing_group[0] and "error_no_encontrado" in str(missing_group[1])
    assert not comps[0] and not actas[0]                                                            # las tools anteriores siguen respondiendo
    assert rules[0] or "hits" in rules[1]                                                           # search_rules: con o sin base documental, sin romperse


class _Table:
    """LLM guionizado: ante «¿cómo va la clasificación?» usa la tabla oficial (no resultados ni actas) y cita la jornada."""

    provider, model = "fake", "fake"

    def __init__(self, gid: int) -> None:
        self.gid, self.calls, self.seen = gid, 0, ""

    def complete(self, system, messages, tools):  # type: ignore[no-untyped-def]
        self.calls += 1
        names = {t["name"] for t in tools}
        if self.calls == 1:
            assert {"get_standings", "get_scorer_table"} <= names and "get_standings" in system and "no los calcules" in system
            return LLMResponse("", [ToolCall("t1", "get_standings", {"group_id": self.gid})])
        self.seen = messages[-1].tool_results[0].content
        return LLMResponse("Tras la jornada 4 (17-10-2099) lidera C.D. EJEMPLO UNO con 10 puntos.", [])


def test_agent_answers_the_standing_question_with_the_official_table_in_one_call(db: Engine, tmp_path: Path) -> None:
    _run(db, "standings", _site(), tmp_path)
    server, llm = create_server(TestClient(create_app(db))), _Table(_gid(db))

    async def go() -> Any:
        async with connect(server._mcp_server) as session:
            return await Agent(llm, SessionMcpClient(session), 5, preload_context=False).run("¿Cómo está la clasificación de mi grupo?")

    res = asyncio.run(go())
    assert res.tool_calls == 1 and res.guardrail is None and "jornada 4" in res.answer
    sent = json.loads(llm.seen)
    assert sent["source"]["round"] == 4 and sent["standings"][0]["points"] == 10 and "null" not in llm.seen


def test_counts_in_tables_match_the_database_after_everything(db: Engine, tmp_path: Path) -> None:
    _run(db, "standings", _site(), tmp_path)
    _run(db, "scorers", _site(), tmp_path)
    with db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM matches")).scalar() == 0                       # no se tocan los datos deportivos
        assert c.execute(text("SELECT count(*) FROM scorer_rows r JOIN scorer_snapshots s ON s.id = r.snapshot_id")).scalar() == 6
