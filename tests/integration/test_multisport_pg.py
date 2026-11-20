"""Fase 1 multideporte sobre PG desechable: deporte obligatorio en la API, aislamiento fútbol sala / fútbol 11, guardas de
importación entre deportes, identidad de equipo por deporte, migración 0009 y alcance de ingesta en planificador y trabajador.

Datos: la liga de fútbol sala del fixture (tipojuego=3) y una copia sintética como fútbol 11 (tipojuego=1) con otros IDs de
competición, grupo y partido, pero los MISMOS CodEquipo (caso que no se ha verificado que no ocurra en la RFFM)."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from futsal.api.app import create_app
from futsal.db.models import (
    Competition,
    CompetitionGroup,
    DataQualityIssue,
    IngestJob,
    Match,
    Team,
    TeamEntity,
)
from futsal.importer.catalog_backfill import run_backfill
from futsal.importer.service import run_import
from futsal.ingestion.queue import ingest_scope, jobs
from futsal.ingestion.queue.planner import gather_state, group_target
from futsal.ingestion.queue.schedule import load_schedule

pytestmark = pytest.mark.integration
FIX = Path(__file__).parents[1] / "fixtures" / "league_two_rounds.json"
ROOT = Path(__file__).parents[2]
FS_COMP, FS_GROUP = "26738243", "26738245"
F11_COMP, F11_GROUP = "26737701", "99000001"  # Preferente Aficionado (código real del catálogo T22 de fútbol 11)
DAY = date(2026, 12, 5)  # día con partidos en el fixture


def f11_league(tmp_path: Path, *, game_type: str = "1", group: str = F11_GROUP, comp: str = F11_COMP) -> Path:
    data: dict[str, Any] = json.loads(FIX.read_text(encoding="utf-8"))
    data["competition"] = {"external_id": comp, "name": "PREFERENTE AFICIONADO"}
    data["group"] = {"external_id": group, "name": "Grupo 1"}
    data["game_type"] = {"external_id": game_type}
    for m in data["matches"]:
        m["external_match_id"] = f"F{m['external_match_id']}"
    path = tmp_path / f"f11_{comp}_{group}_{game_type}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture()
def both(engine: Engine, tmp_path: Path) -> Engine:
    with engine.begin() as c:  # el TRUNCATE común no incluye las tablas de identidad (0007)
        c.execute(text("TRUNCATE team_entities, clubs RESTART IDENTITY CASCADE"))
    assert run_import(engine, FIX).status == "success"
    assert run_import(engine, f11_league(tmp_path)).status == "success"
    return engine


@pytest.fixture()
def client(both: Engine) -> TestClient:
    return TestClient(create_app(both, today=lambda: date(2026, 11, 19)))


def ids(engine: Engine, model: Any, external_id: str) -> int:
    with Session(engine) as s:
        return int(s.scalar(select(model.id).where(model.external_id == external_id)))


def f11_match_id(engine: Engine) -> int:
    with Session(engine) as s:
        return int(s.scalar(select(Match.id).where(Match.external_id.like("F%")).limit(1)))


# --------------------------------------------------------------------------- modelo e importación


def test_sport_is_set_from_the_official_game_type(both: Engine) -> None:
    with Session(both) as s:
        got = {c.external_id: (c.modality, c.game_type_code) for c in s.scalars(select(Competition))}
    assert got == {FS_COMP: ("futsal", "3"), F11_COMP: ("football11", "1")}


def test_new_competition_without_game_type_is_rejected(engine: Engine, tmp_path: Path) -> None:
    data = json.loads(FIX.read_text(encoding="utf-8"))
    data.pop("game_type")
    path = tmp_path / "sin_tipo.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    r = run_import(engine, path)
    assert r.status == "failed" and "sin deporte" in (r.error or "")
    with Session(engine) as s:
        assert s.scalar(select(Competition.id)) is None  # nunca se supone fútbol sala


def test_existing_competition_keeps_its_sport_without_game_type(both: Engine, tmp_path: Path) -> None:
    data = json.loads(FIX.read_text(encoding="utf-8"))
    data.pop("game_type")  # fichero antiguo: la competición ya existe y su deporte manda
    path = tmp_path / "antiguo.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert run_import(both, path).status == "success"


def test_cross_sport_competition_is_a_recorded_conflict_and_changes_nothing(both: Engine, tmp_path: Path) -> None:
    with Session(both) as s:
        before = s.scalar(select(text("count(*)")).select_from(Match))
    r = run_import(both, f11_league(tmp_path, game_type="3"))  # mismo ID de competición, otro deporte
    assert r.status == "failed" and r.issue_codes == {"sport_conflict": 1}
    with Session(both) as s:
        assert s.scalar(select(Competition.modality).where(Competition.external_id == F11_COMP)) == "football11"
        assert s.scalar(select(text("count(*)")).select_from(Match)) == before
        issue = s.scalars(select(DataQualityIssue).where(DataQualityIssue.code == "sport_conflict")).one()
        assert issue.entity_type == "competition" and issue.details["incoming_sport"] == "futsal"


def test_group_is_never_moved_to_a_competition_of_another_sport(both: Engine, tmp_path: Path) -> None:
    r = run_import(both, f11_league(tmp_path, group=FS_GROUP, comp="26737706"))  # grupo de fútbol sala en una competición de F11
    assert r.status == "failed" and r.issue_codes == {"sport_conflict": 1}
    with Session(both) as s:
        g = s.scalars(select(CompetitionGroup).where(CompetitionGroup.external_id == FS_GROUP)).one()
        assert s.get(Competition, g.competition_id).external_id == FS_COMP


def test_same_team_code_in_two_sports_are_two_entities(both: Engine) -> None:
    run_backfill(both, inventories=[])
    with Session(both) as s:
        rows = s.execute(select(TeamEntity.external_code, TeamEntity.sport)).all()
        codes = {c for c, _ in rows}
        assert {sp for _, sp in rows} == {"futsal", "football11"}
        assert len(rows) == 2 * len(codes)  # cada CodEquipo, una entidad por deporte: nunca se fusionan
        for t, sport in s.execute(select(Team, Competition.modality).join(CompetitionGroup, CompetitionGroup.id == Team.group_id)
                                  .join(Competition, Competition.id == CompetitionGroup.competition_id)):
            assert s.get(TeamEntity, t.team_entity_id).sport == sport


# --------------------------------------------------------------------------- API: deporte obligatorio y aislamiento

ENDPOINTS = ["/competitions", "/teams?name=agrup", "/crests", "/tables/coverage", "/knowledge/search?q=penalti"]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_v1_requires_sport(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 400 and "sport" in r.json()["detail"]


@pytest.mark.parametrize("path", ["/v2/matches?date_from=2026-12-05", "/v2/rankings/scorers", "/v2/players?name=juan",
                                  "/v2/officials?name=juan", "/v2/clubs/search?name=agrupacion", "/v2/rankings/teams?metric=wins"])
def test_v2_requires_sport(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 422 and "sport" in r.json()["detail"]


def test_group_and_match_endpoints_require_sport(client: TestClient, both: Engine) -> None:
    gid, mid = ids(both, CompetitionGroup, FS_GROUP), ids(both, Match, "5575696")
    for path in (f"/groups/{gid}/matches", f"/groups/{gid}/rounds", f"/groups/{gid}/teams", f"/groups/{gid}/standings",
                 f"/groups/{gid}/scorers", f"/groups/{gid}/freshness", f"/matches/{mid}", f"/matches/{mid}/report"):
        assert client.get(path).status_code == 400, path
    assert client.get("/competitions", params={"sport": "tenis"}).status_code == 400


def test_competitions_are_isolated_by_sport(client: TestClient) -> None:
    fs = client.get("/competitions", params={"sport": "futsal"}).json()["items"]
    f11 = client.get("/competitions", params={"sport": "football11"}).json()["items"]
    both_ = client.get("/competitions", params={"sport": "all"}).json()["items"]
    assert [c["external_id"] for c in fs] == [FS_COMP] and "sport" not in fs[0]  # un deporte: formato de siempre
    assert [c["external_id"] for c in f11] == [F11_COMP]
    assert sorted((c["external_id"], c["sport"]) for c in both_) == [(F11_COMP, "football11"), (FS_COMP, "futsal")]


def test_team_search_never_crosses_sports(client: TestClient) -> None:
    fs = client.get("/teams", params={"sport": "futsal", "name": "agrupacion"}).json()["items"]
    f11 = client.get("/teams", params={"sport": "football11", "name": "agrupacion"}).json()["items"]
    assert fs and f11 and {t["competition"] for t in fs} == {"PRIMERA DIVISION AUTONOMICA AFICIONADO FS"}
    assert {t["competition"] for t in f11} == {"PREFERENTE AFICIONADO"}
    assert not {t["group_id"] for t in fs} & {t["group_id"] for t in f11}


def test_ids_of_another_sport_are_not_found(client: TestClient, both: Engine) -> None:
    f11_gid, f11_cid, f11_mid = ids(both, CompetitionGroup, F11_GROUP), ids(both, Competition, F11_COMP), f11_match_id(both)
    for path in (f"/groups/{f11_gid}/matches", f"/groups/{f11_gid}/rounds", f"/groups/{f11_gid}/teams", f"/groups/{f11_gid}/standings",
                 f"/groups/{f11_gid}/scorer-table", f"/groups/{f11_gid}/match-events", f"/groups/{f11_gid}/scorers",
                 f"/groups/{f11_gid}/players", f"/groups/{f11_gid}/freshness", f"/competitions/{f11_cid}/groups",
                 f"/competitions/{f11_cid}/freshness", f"/competitions/{f11_cid}/scorer-table", f"/matches/{f11_mid}",
                 f"/matches/{f11_mid}/report", f"/tables/coverage?competition_id={f11_cid}"):
        base, _, query = path.partition("?")  # httpx sustituye la query de la URL por `params`: se pasan juntos
        r = client.get(base, params={"sport": "futsal", "name": "juan", **dict(parse_qsl(query))})  # `name` solo lo usa /players
        assert r.status_code == 404 and "fútbol sala" in r.json()["detail"], path
    assert client.get(f"/groups/{f11_gid}/matches", params={"sport": "football11"}).status_code == 200
    assert client.get(f"/matches/{f11_mid}", params={"sport": "football11"}).status_code == 200


def test_group_matches_stay_in_their_sport(client: TestClient, both: Engine) -> None:
    fs_gid = ids(both, CompetitionGroup, FS_GROUP)
    rows = client.get(f"/groups/{fs_gid}/matches", params={"sport": "futsal"}).json()["items"]
    assert len(rows) == 14 and all(r["group_id"] == fs_gid for r in rows)


def test_coverage_lists_only_the_requested_sport(client: TestClient, both: Engine) -> None:
    fs = client.get("/tables/coverage", params={"sport": "futsal"}).json()
    assert [g["group_id"] for g in fs["items"]] == [ids(both, CompetitionGroup, FS_GROUP)]


def test_v2_matches_by_day_never_mix_sports(client: TestClient) -> None:
    q = {"date_from": DAY.isoformat(), "limit": 100}
    fs = client.get("/v2/matches", params={**q, "sport": "futsal"}).json()
    f11 = client.get("/v2/matches", params={**q, "sport": "football11"}).json()
    both_ = client.get("/v2/matches", params={**q, "sport": "all"}).json()
    assert fs["count"] > 0 and fs["count"] == f11["count"] and both_["count"] == fs["count"] + f11["count"]
    assert all("FS" in r["competition"]["name"] for r in fs["items"]) and all("sport" not in r for r in fs["items"])
    assert all(r["competition"]["name"] == "PREFERENTE AFICIONADO" for r in f11["items"])
    assert {r["sport"] for r in both_["items"]} == {"futsal", "football11"}  # ámbito combinado: cada fila dice su deporte
    compact = client.get("/v2/matches", params={**q, "sport": "all", "compact": True}).json()["items"]
    assert {r["sport"] for r in compact} == {"futsal", "football11"}


def test_v2_clubs_and_rankings_respect_sport(client: TestClient) -> None:
    fs = client.get("/v2/clubs/search", params={"sport": "futsal", "name": "agrupacion deportiva colegio calasancio"}).json()
    names = json.dumps(fs, ensure_ascii=False)
    assert "PREFERENTE AFICIONADO\"" not in names and "FS" in names
    r = client.get("/v2/rankings/teams", params={"sport": "football11", "metric": "wins"}).json()
    assert all("FS" not in json.dumps(i, ensure_ascii=False) for i in r["items"])


# --------------------------------------------------------------------------- alcance de ingesta


def test_planner_only_plans_enabled_competitions(both: Engine) -> None:
    with Session(both) as s:
        state = gather_state(s, "rffm", "22", "3", load_schedule())
    assert [g.group for g in state.groups] == [FS_GROUP]  # fútbol 11 declarado pero deshabilitado (Fase 1)


def test_planner_includes_football11_only_when_its_entry_is_enabled(both: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = json.loads(ingest_scope.SCOPE_PATH.read_text(encoding="utf-8"))
    raw["sports"]["football11"]["enabled"] = True
    for e in raw["sports"]["football11"]["competitions"]:
        e["enabled"] = e["name"] == "PREFERENTE AFICIONADO"
    with Session(both) as s:  # la competición de la BD necesita su CodigoCategoria para casar con la entrada
        s.execute(text("UPDATE competitions SET category_code = '40054' WHERE external_id = :c"), {"c": F11_COMP})
        s.commit()
    monkeypatch.setattr("futsal.ingestion.queue.planner.load_scope", lambda: ingest_scope.parse_scope(raw))
    with Session(both) as s:
        state = gather_state(s, "rffm", "22", "3", load_schedule())
    assert sorted(g.group for g in state.groups) == sorted([FS_GROUP, F11_GROUP])
    assert {g.group: g.game_type for g in state.groups}[F11_GROUP] == "1"


def test_worker_gate_rejects_out_of_scope_jobs_without_network(both: Engine) -> None:
    scope = ingest_scope.load_scope()
    with Session(both) as s:
        assert ingest_scope.gate_target(s, scope, group_target("22", FS_COMP, FS_GROUP, "3")) is None
        blocked = ingest_scope.gate_target(s, scope, group_target("22", F11_COMP, F11_GROUP, "1"))
        assert blocked is not None and "football11" in blocked
        assert "fuera del alcance" in str(ingest_scope.gate_target(s, scope, {"season": "22", "game_type": "2"}))


def test_worker_fails_out_of_scope_job_and_never_fetches(both: Engine, tmp_path: Path) -> None:
    from futsal.ingestion.queue.worker import run_worker

    class NoNetwork:
        source, requests, max_requests = "rffm", 0, 10

        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"el trabajador no debe usar la red ({name})")

    factory = sessionmaker(both)
    with both.begin() as c:  # la cola y el interruptor de la fuente no los vacía el TRUNCATE común
        c.execute(text("TRUNCATE ingest_jobs, ingest_controls, raw_fetches RESTART IDENTITY CASCADE"))
    with factory() as s, s.begin():
        jobs.enqueue(s, "rffm", "calendar", group_target("22", F11_COMP, F11_GROUP, "1"), priority=50,
                     now=datetime(2026, 11, 19, tzinfo=UTC))
    report = run_worker(factory, NoNetwork(), until_empty=True, work_dir=tmp_path, now=lambda: datetime(2026, 11, 19, 1, tzinfo=UTC))
    with factory() as s:
        job = s.scalars(select(IngestJob)).one()
    assert job.status == "dead" and "fuera del alcance de ingesta" in (job.last_error or "")
    assert report.done == 0 and report.stop_reason == "empty"


# --------------------------------------------------------------------------- migración 0009


@pytest.fixture()
def lifecycle(pg_engine: Engine) -> Any:
    admin = create_engine(pg_engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    name = "futsal_test_m0009"
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(pg_engine.url.render_as_string(hide_password=False)).set(database=name).render_as_string(hide_password=False)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    eng = create_engine(url)
    yield eng, cfg
    eng.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


def test_migration_0009_backfills_sport_and_rolls_back(lifecycle: Any) -> None:
    eng, cfg = lifecycle
    command.upgrade(cfg, "0008")
    with eng.begin() as c:  # datos «anteriores»: una competición sin game_type_code (import-league antiguo) y otra con '3'
        c.execute(text("INSERT INTO seasons(source, external_id, first_seen_at, last_seen_at) VALUES ('rffm','22', now(), now())"))
        c.execute(text("INSERT INTO competitions(source, external_id, season_id, name, game_type_code, first_seen_at, last_seen_at) "
                       "VALUES ('rffm','A',1,'LIGA A FS',NULL,now(),now()), ('rffm','B',1,'LIGA B FS','3',now(),now())"))
        c.execute(text("INSERT INTO team_entities(source, external_code) VALUES ('rffm','T1')"))
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        assert dict(c.execute(text("SELECT external_id, modality FROM competitions")).all()) == {"A": "futsal", "B": "futsal"}
        assert c.execute(text("SELECT classification->'modality'->>'source' FROM competitions WHERE external_id='A'")).scalar() == \
            "migration_0009"
        assert c.execute(text("SELECT sport FROM team_entities")).scalar() == "futsal"
        assert c.execute(text("SELECT column_default FROM information_schema.columns "
                              "WHERE table_name='competitions' AND column_name='modality'")).scalar() is None
    with pytest.raises(IntegrityError), eng.begin() as c:  # sin deporte no hay alta posible
        c.execute(text("INSERT INTO competitions(source, external_id, season_id, name, first_seen_at, last_seen_at) "
                       "VALUES ('rffm','C',1,'X',now(),now())"))
    with pytest.raises(IntegrityError), eng.begin() as c:
        c.execute(text("UPDATE competitions SET modality='unknown' WHERE external_id='B'"))
    command.downgrade(cfg, "0008")
    with eng.connect() as c:
        cols = {r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='team_entities'"))}
        assert "sport" not in cols and c.execute(text("SELECT count(*) FROM competitions")).scalar() == 2
    command.upgrade(cfg, "head")


def test_migration_0009_refuses_unmapped_game_type(lifecycle: Any) -> None:
    eng, cfg = lifecycle
    command.upgrade(cfg, "0008")
    with eng.begin() as c:
        c.execute(text("INSERT INTO seasons(source, external_id, first_seen_at, last_seen_at) VALUES ('rffm','22', now(), now())"))
        c.execute(text("INSERT INTO competitions(source, external_id, season_id, name, game_type_code, first_seen_at, last_seen_at) "
                       "VALUES ('rffm','X',1,'RARA','9',now(),now())"))
    with pytest.raises(Exception, match="sin correspondencia"):
        command.upgrade(cfg, "head")


def test_migration_0010_adds_substitutions_and_staff_labels_and_rolls_back(lifecycle: Any) -> None:
    eng, cfg = lifecycle
    command.upgrade(cfg, "0009")
    cols = lambda t: {r[0] for r in eng.connect().execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": t})}
    assert "related_player_id" not in cols("match_events") and "role_label" not in cols("match_staff")
    command.upgrade(cfg, "head")
    assert {"related_player_id", "related_player_external_id", "related_player_name"} <= cols("match_events")
    assert "role_label" in cols("match_staff")
    check = eng.connect().execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_match_events_type_valid'")).scalar()
    assert "substitution" in check and "own_goal" in check
    command.downgrade(cfg, "0009")  # sin sustituciones guardadas: se puede bajar
    assert "related_player_id" not in cols("match_events")
    command.upgrade(cfg, "head")
