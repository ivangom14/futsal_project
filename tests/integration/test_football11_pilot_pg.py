"""Fase 2 · piloto de fútbol 11 reproducido sin red: la MISMA cola, planificador y trabajador que fútbol sala, sobre una RFFM
simulada con las respuestas ANONIMIZADAS del piloto real (Preferente Aficionado, grupo 1; `tests/fixtures/rffm_f11/`).

Comprueba: alcance aislado (solo la competición y el grupo habilitados), importación como football11, actas de fútbol 11 (11
titulares, esquema declarado, sustituciones no incorporadas), aislamiento por deporte en la API, idempotencia y que el alcance
operativo deja todo deshabilitado."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from futsal.api.app import create_app
from futsal.ingestion.queue import ingest_scope, jobs
from futsal.ingestion.queue.breaker import CircuitBreaker
from futsal.ingestion.queue.controls import load_config
from futsal.ingestion.queue.fetch import Fetcher
from futsal.ingestion.queue.planner import tick
from futsal.ingestion.queue.ratelimit import RateLimiter
from futsal.ingestion.queue.schedule import load_schedule
from futsal.ingestion.queue.worker import run_worker

pytestmark = pytest.mark.integration
F11 = Path(__file__).parents[1] / "fixtures" / "rffm_f11"
FS_LEAGUE = Path(__file__).parents[1] / "fixtures" / "league_two_rounds.json"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
COMP, GROUP = "26737701", "26737702"


class Clock:
    def __init__(self) -> None:
        self.t = NOW

    def now(self) -> datetime:
        return self.t

    def mono(self) -> float:
        return (self.t - NOW).total_seconds()

    def sleep(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class Site:
    """Responde con las fixtures anonimizadas; lo que el piloto no descargó responde 404 (nunca se inventa)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        q, path = request.url.params, request.url.path
        file = {
            "/api/competitions": F11 / f"api_competitions_s{q.get('temporada')}_t{q.get('tipojuego')}.json",
            "/api/groups": F11 / f"api_groups_{q.get('competicion')}.json",
            "/competicion/calendario": F11 / f"calendario_g{q.get('grupo')}.html",
            "/competicion/clasificaciones": F11 / f"clasificaciones_g{q.get('grupo')}.html",
            "/competicion/goleadores": F11 / f"goleadores_g{q.get('grupo')}.html",
            "/api/results": F11 / f"api_results_g{q.get('idGroup')}_r{q.get('round')}.json",
        }.get(path)
        if path.startswith("/acta-partido/"):
            code = path.rsplit("/", 1)[1]
            file = F11 / f"acta_{code}.html" if (F11 / f"acta_{code}.html").exists() else F11 / f"acta_{code}_vacia.html"
        if file is None or not file.exists():
            return httpx.Response(404, content=b"not found")
        ctype = "application/json" if file.suffix == ".json" else "text/html; charset=utf-8"
        return httpx.Response(200, content=file.read_bytes(), headers={"content-type": ctype})


def pilot_scope(tmp_path: Path) -> Path:
    raw = json.loads(ingest_scope.SCOPE_PATH.read_text(encoding="utf-8"))
    raw["sports"]["football11"]["enabled"] = True
    for e in raw["sports"]["football11"]["competitions"]:
        e["enabled"] = e["name"] == "PREFERENTE AFICIONADO"
        if e["enabled"]:
            e["groups"] = [GROUP]
    path = tmp_path / "pilot_scope.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


class Pilot:
    def __init__(self, engine: Engine, tmp_path: Path) -> None:
        self.engine, self.tmp_path, self.clock, self.site = engine, tmp_path, Clock(), Site()
        self.sf = sessionmaker(engine, expire_on_commit=False)
        self.cfg = load_config()

    def work(self, budget: int = 200) -> Any:
        session = self.sf()
        fetcher = Fetcher(session, "rffm", RateLimiter(self.cfg.min_interval_s, self.cfg.max_backoff_s, self.clock.mono, self.clock.sleep),
                          CircuitBreaker("rffm", self.cfg.breaker), client=httpx.Client(transport=httpx.MockTransport(self.site)),
                          max_requests=budget, snapshot_dir=self.tmp_path / "snap", network_authorized=True, now=self.clock.now)
        try:
            return run_worker(self.sf, fetcher, now=self.clock.now, work_dir=self.tmp_path / "work", sleep=self.clock.sleep, config=self.cfg)
        finally:
            session.close()

    def tick(self) -> None:
        with self.sf() as s, s.begin():
            tick(s, "rffm", "22", "1", load_schedule(), self.clock.now(), exclude_kinds=("discover_catalog", "discover_groups"))

    def rows(self, sql: str) -> list[Any]:
        with self.engine.connect() as c:
            return list(c.execute(text(sql)).all())


@pytest.fixture()
def pilot(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Pilot:
    with engine.begin() as c:
        c.execute(text("TRUNCATE ingest_jobs, raw_fetches, ingest_controls, team_entities, clubs RESTART IDENTITY CASCADE"))
    monkeypatch.setenv(ingest_scope.SCOPE_ENV, str(pilot_scope(tmp_path)))
    p = Pilot(engine, tmp_path)
    with p.sf() as s, s.begin():
        jobs.enqueue(s, "rffm", "discover_catalog", {"season": "22", "game_type": "1"}, priority=40, now=p.clock.now())
    p.work()
    p.tick()
    p.work()
    return p


def test_only_the_enabled_competition_and_group_are_downloaded(pilot: Pilot) -> None:
    assert pilot.rows("SELECT external_id, modality, game_type_code FROM competitions") == [(COMP, "football11", "1")]
    assert len(pilot.rows("SELECT id FROM competition_groups")) == 4  # metadatos de los 4 grupos oficiales
    downloaded = {u.split("?")[0].rsplit("/", 1)[-1] for u in pilot.site.calls}
    assert not [u for u in pilot.site.calls if "grupo=" in u and f"grupo={GROUP}" not in u]  # ningún otro grupo
    assert not [u for u in pilot.site.calls if "idGroup=" in u and f"idGroup={GROUP}" not in u]
    assert "groups" in downloaded and len([u for u in pilot.site.calls if "/api/groups" in u]) == 1  # solo Preferente Aficionado


def test_pilot_group_is_imported_as_football11_with_official_data(pilot: Pilot) -> None:
    assert pilot.rows("SELECT count(*) FROM teams")[0][0] == 18
    assert pilot.rows("SELECT count(*) FROM matches WHERE status = 'finished'")[0][0] == 18  # jornadas 9 y 10 del piloto
    assert pilot.rows("SELECT count(*) FROM standings_rows")[0][0] == 18
    assert pilot.rows("SELECT sum(goals) FROM scorer_rows")[0][0] == 286
    acta = pilot.rows("SELECT home_formation, away_formation FROM match_report_observations")
    assert acta == [("1-4-3-3", "1-4-4-2")]  # esquema DECLARADO en el acta oficial
    starters = pilot.rows("SELECT count(*) FROM match_players WHERE lineup_status = 'starter'")[0][0]
    assert starters == 22
    # Fase 3C: sustituciones y «otros técnicos» interpretados (antes, incidencia `report_section_not_parsed`)
    assert pilot.rows("SELECT count(*) FROM data_quality_issues WHERE code = 'report_section_not_parsed'")[0][0] == 0
    assert pilot.rows("SELECT count(*), count(related_player_id), count(player_id) FROM match_events WHERE event_type = 'substitution'")[0] == (10, 10, 10)
    assert pilot.rows("SELECT role, role_label FROM match_staff WHERE role = 'other_staff'") == [("other_staff", "ENCARGADO DE MATERIAL")]


def test_api_isolates_the_pilot_by_sport_and_tells_the_truth_about_substitutions(pilot: Pilot) -> None:
    c = TestClient(create_app(pilot.engine, today=lambda: date(2026, 11, 20)))
    gid = pilot.rows(f"SELECT id FROM competition_groups WHERE external_id = '{GROUP}'")[0][0]
    assert c.get("/competitions", params={"sport": "futsal"}).json()["count"] == 0
    assert c.get(f"/groups/{gid}/standings", params={"sport": "futsal"}).status_code == 404
    st = c.get(f"/groups/{gid}/standings", params={"sport": "football11"}).json()
    assert st["status"] == "ok" and len(st["standings"]) == 18
    mid = c.get(f"/groups/{gid}/matches", params={"sport": "football11", "has_report": True}).json()["items"][0]["id"]
    rep = c.get(f"/matches/{mid}/report", params={"sport": "football11", "sections": "players,goals,staff"}).json()
    assert "sustituciones" not in rep["not_in_source"] and "not_ingested" not in rep
    assert sum(p["lineup"] == "starter" for p in rep["players"]) == 22
    subs = rep["substitutions"]  # van con la alineación en fútbol 11
    assert len(subs) == 10 and all(x["type"] == "substitution" and x["player"] and x["player_out"] for x in subs)
    assert len(rep["goals"]) == 6 and not rep["consistency"].get("issues")  # las sustituciones nunca cuentan como goles
    assert {"role": "other_staff", "role_label": "ENCARGADO DE MATERIAL"}.items() <= next(
        x for x in rep["staff"] if x["role"] == "other_staff").items()


def test_pilot_replay_is_idempotent(pilot: Pilot) -> None:
    q = ("SELECT md5(string_agg(concat_ws(',', external_id, status, home_score, away_score, scheduled_at), '|' ORDER BY external_id)) "
         "FROM matches")
    before, obs = pilot.rows(q)[0][0], pilot.rows("SELECT count(*) FROM match_observations")[0][0]
    with pilot.engine.begin() as c:  # se fuerza a repetir todo lo hecho (mismas respuestas)
        c.execute(text("UPDATE ingest_jobs SET status = 'cancelled' WHERE status IN ('pending', 'failed')"))
    for kind, target in pilot.rows("SELECT kind, target FROM ingest_jobs WHERE status = 'done' AND kind IN ('round_results', 'calendar')"):
        with pilot.sf() as s, s.begin():
            jobs.enqueue(s, "rffm", kind, target, priority=90, now=pilot.clock.now())
    pilot.work()
    assert pilot.rows(q)[0][0] == before and pilot.rows("SELECT count(*) FROM match_observations")[0][0] == obs


def test_operational_scope_keeps_football11_disabled(pilot: Pilot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ingest_scope.SCOPE_ENV)  # el operativo: el que usa la programación
    with pilot.sf() as s, s.begin():
        jobs.enqueue(s, "rffm", "calendar", {"season": "22", "competition": COMP, "group": GROUP, "game_type": "1"}, priority=99,
                     now=pilot.clock.now())
    calls = len(pilot.site.calls)
    pilot.work()
    assert len(pilot.site.calls) == calls  # ni una petición
    dead = pilot.rows("SELECT last_error FROM ingest_jobs WHERE status = 'dead' AND kind = 'calendar'")
    assert dead and "fuera del alcance" in dead[-1][0]


# --------------------------------------------------------------------------- agente de extremo a extremo (LLM guionizado, sin coste)


class Script:
    provider, model = "fake", "fake-model"

    def __init__(self, steps: list[Any]) -> None:
        self.steps, self.systems, self.seen = list(steps), [], []

    def complete(self, system: str, messages: list[Any], tools: list[dict[str, Any]]) -> Any:
        self.systems.append(system)
        self.seen.append(messages)
        return self.steps.pop(0)


def _with_homonymous_futsal_league(pilot: Pilot) -> None:
    from futsal.importer.service import run_import
    data = json.loads(FS_LEAGUE.read_text(encoding="utf-8"))
    data["competition"] = {"external_id": "26738246", "name": "PREFERENTE AFICIONADO FS"}  # código real de la homónima de FS
    path = pilot.tmp_path / "fs_preferente.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert run_import(pilot.engine, path).status == "success"


def _converse(pilot: Pilot, turns: list[tuple[str, str, list[Any]]]) -> list[tuple[Any, Script, list[tuple[str, str | None]]]]:
    import asyncio

    from mcp.shared.memory import create_connected_server_and_client_session as connect

    from futsal.agent.agent import Agent
    from futsal.agent.mcp_client import SessionMcpClient
    from futsal.mcp_server.server import create_server

    seen: list[httpx.Request] = []
    client = TestClient(create_app(pilot.engine, today=lambda: date(2026, 11, 20)))
    client.event_hooks["request"].append(seen.append)

    async def go() -> list[tuple[Any, Script, list[tuple[str, str | None]]]]:
        out = []
        async with connect(create_server(client)._mcp_server) as session:
            for sport, question, steps in turns:
                llm, start = Script(steps), len(seen)
                agent = Agent(llm, SessionMcpClient(session), 6)
                res = await agent.run(question, sport=sport)
                out.append((res, llm, [(r.url.path, r.url.params.get("sport")) for r in seen[start:]]))
        return out

    return asyncio.run(go())


def test_agent_answers_football11_with_football11_data_only(pilot: Pilot) -> None:
    from futsal.agent.llm import LLMResponse, ToolCall
    _with_homonymous_futsal_league(pilot)
    gid = pilot.rows(f"SELECT id FROM competition_groups WHERE external_id = '{GROUP}'")[0][0]
    fs_gid = pilot.rows("SELECT id FROM competition_groups WHERE external_id = '26738245'")[0][0]
    [(f11, f11_llm, f11_calls), (_fs, fs_llm, fs_calls), (switch, sw_llm, sw_calls)] = _converse(pilot, [
        ("football11", "¿Quién es el líder de Preferente Aficionado, grupo 1?",
         [LLMResponse("", [ToolCall("c1", "get_standings", {"group_id": gid})]), LLMResponse("Lidera el primero.", [])]),
        ("futsal", "¿Quién lidera la Preferente Aficionado?",
         [LLMResponse("", [ToolCall("c1", "get_standings", {"group_id": gid})]), LLMResponse("No consta.", [])]),
        ("football11", "¿Y cómo va Preferente Aficionado de fútbol sala?", []),
    ])
    # fútbol 11: catálogo, contexto y herramientas solo de fútbol 11; la clasificación oficial llega al modelo
    assert "PREFERENTE AFICIONADO" in f11_llm.systems[0] and "PREFERENTE AFICIONADO FS" not in f11_llm.systems[0]
    assert f11_calls and {sp for _, sp in f11_calls} == {"football11"}
    tool_text = " ".join(r.content for m in f11_llm.seen[1] if m.role == "tool" for r in m.tool_results)
    assert '"points":23' in tool_text.replace(' ', '') and f11.answer == "Lidera el primero."
    # fútbol sala: la homónima de FS; el ID del grupo de fútbol 11 NO devuelve datos (404 en la API) aunque el modelo lo invente
    assert "PREFERENTE AFICIONADO FS" in fs_llm.systems[0] and f"group_id={gid}" not in fs_llm.systems[0] and f"group_id={fs_gid}" in fs_llm.systems[0]
    assert {sp for _, sp in fs_calls} == {"futsal"}
    fs_tool = " ".join(r.content for m in fs_llm.seen[1] if m.role == "tool" for r in m.tool_results)
    assert "error_no_encontrado" in fs_tool and '"points":23' not in fs_tool.replace(' ', '')
    # mención explícita del otro deporte: propuesta de cambio sin LLM y sin datos de fútbol sala
    assert switch.sport_switch == "futsal" and sw_llm.systems == [] and {sp for _, sp in sw_calls} <= {"football11"}
