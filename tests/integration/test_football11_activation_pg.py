"""Fase 3E · activación operativa del fútbol 11 (12 competiciones) con la cola existente, sin red (fixtures reales de catálogo y
grupos de la Fase 2; `tests/fixtures/rffm_f11/`).

Comprueba el alcance operativo real (`config/ingest_scope.json`): las 12 habilitadas, las otras 31 fuera, CERO actas de fútbol 11
(bloqueadas antes de planificar, de encolar y de ejecutar), goleadores juveniles solo en agregado (`scorers=false`), lotes por
competición en el trabajador, el tipo de juego de cada grupo (nunca el del argumento) y fútbol sala sin cambios."""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from tests.integration.test_football11_coverage_pg import EXPECTED, JUVENILES, run
from tests.integration.test_football11_pilot_pg import GROUP, Pilot

from futsal.ingestion.queue import ingest_scope, jobs
from futsal.ingestion.queue.planner import gather_state, tick
from futsal.ingestion.queue.schedule import load_schedule

pytestmark = pytest.mark.integration
PREFERENTE = "26737701"


@pytest.fixture()
def ops(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Pilot:
    """Alcance OPERATIVO tal cual (sin fichero aislado): descubrimiento hecho, calendarios pendientes."""
    with engine.begin() as c:
        c.execute(text("TRUNCATE ingest_jobs, raw_fetches, ingest_controls, team_entities, clubs RESTART IDENTITY CASCADE"))
    monkeypatch.delenv(ingest_scope.SCOPE_ENV, raising=False)
    p = Pilot(engine, tmp_path)
    with p.sf() as s, s.begin():
        jobs.enqueue(s, "rffm", "discover_catalog", {"season": "22", "game_type": "1"}, priority=40, now=p.clock.now())
    run(p, kinds=["discover_catalog", "discover_groups"])
    return p


def test_operational_scope_enables_exactly_the_twelve_without_reports() -> None:
    raw = json.loads(ingest_scope.SCOPE_PATH.read_text(encoding="utf-8"))
    scope = ingest_scope.parse_scope(raw)
    f11 = scope.sports["football11"]
    assert f11.enabled and len(f11.entries) == 12 and all(e.enabled for e in f11.entries)
    assert not any(e.reports for e in f11.entries)  # 3E: carga básica; las actas de F11 son una ampliación posterior
    juvenile = {e.name for e in f11.entries if not e.scorers}
    assert juvenile == {"NACIONAL JUVENIL", "PRIMERA DIVISIÓN AUTONÓMICA JUVENIL", "PREFERENTE JUVENIL", "PRIMERA JUVENIL",
                        "SEGUNDA JUVENIL"}
    assert scope.status("football11", "PRIMERA CADETE", "40065") == ingest_scope.OUT_OF_SCOPE  # una de las otras 31
    fs = scope.sports["futsal"]  # fútbol sala, exactamente como antes
    assert fs.enabled and fs.all_competitions and fs.reports and fs.game_type == "3"
    assert scope.allows_reports("futsal", "PRIMERA JUVENIL", None) and scope.allows_scorers("futsal", "PRIMERA JUVENIL", None)


def test_operational_load_discovers_93_groups_and_never_plans_or_runs_a_report(ops: Pilot) -> None:
    rows = ops.rows("SELECT c.external_id, count(g.id), sum(g.expected_teams) FROM competitions c "
                    "JOIN competition_groups g ON g.competition_id = c.id GROUP BY c.external_id")
    assert {r[0]: (r[1], r[2]) for r in rows} == EXPECTED
    with Session(ops.engine) as s:
        state = gather_state(s, "rffm", "22", "1", load_schedule())
    assert len(state.groups) == 93 and not any(g.reports_allowed for g in state.groups)
    run(ops, max_requests=80)  # SIN --max-reports ni --exclude-kinds: lo que hará la programación compartida
    assert not [u for u in ops.site.calls if "/acta-partido/" in u]
    assert ops.rows("SELECT count(*) FROM ingest_jobs WHERE kind = 'match_report'")[0][0] == 0
    with Session(ops.engine) as s:  # un acta encolada a mano se rechaza sin red
        target = {"season": "22", "competition": PREFERENTE, "group": GROUP, "game_type": "1", "match": "1"}
        assert "política de actas" in str(ingest_scope.gate_target(s, ingest_scope.load_scope(), target))


def test_juvenile_scorers_are_never_planned_enqueued_or_fetched(ops: Pilot) -> None:
    with Session(ops.engine) as s:
        state = gather_state(s, "rffm", "22", "1", load_schedule())
    juvenile = [g for g in state.groups if g.competition in JUVENILES]
    assert len(juvenile) == 52 and all(g.has_scorers is False for g in juvenile)
    assert all(g.has_scorers is not False for g in state.groups if g.competition not in JUVENILES)
    with Session(ops.engine) as s, s.begin():
        result = tick(s, "rffm", "22", "1", load_schedule(), ops.clock.now(), exclude_kinds=("discover_catalog", "discover_groups"))
    planned = {(sp.kind, sp.target["competition"]) for sp in result.specs}
    assert not any(k == "scorers" and c in JUVENILES for k, c in planned)
    assert any(k == "standings" and c in JUVENILES for k, c in planned)  # la clasificación oficial (agregada) sí
    g = juvenile[0]
    with ops.sf() as s, s.begin():  # encolado a mano: el trabajador lo rechaza sin red
        jobs.enqueue(s, "rffm", "scorers", g.target, priority=200, now=ops.clock.now())
    before = len(ops.site.calls)
    run(ops, max_requests=1, kinds=["scorers"], competitions=[g.competition])
    assert len(ops.site.calls) == before
    assert "scorers=false" in ops.rows("SELECT last_error FROM ingest_jobs WHERE kind = 'scorers' AND priority = 200")[0][0]


def test_worker_batches_by_competition_and_leaves_the_rest_pending(ops: Pilot) -> None:
    pending = ops.rows("SELECT count(*) FROM ingest_jobs WHERE kind = 'calendar' AND status = 'pending'")[0][0]
    assert pending == 93
    report = run(ops, max_requests=50, kinds=["calendar"], competitions=[PREFERENTE])
    done = ops.rows("SELECT DISTINCT target->>'competition' FROM ingest_jobs WHERE kind = 'calendar' AND status = 'done'")
    assert done == [(PREFERENTE,)] and report.requests_by_kind == {"calendar": 4}
    left = ops.rows("SELECT count(*) FROM ingest_jobs WHERE kind = 'calendar' AND status = 'pending'")[0][0]
    assert left == 89  # nada se pierde ni se duplica: la siguiente tanda lo toma


def test_planner_uses_each_competition_game_type_not_the_argument(ops: Pilot) -> None:
    """Defecto corregido en la 3E: `tick --game-type 1` etiquetaba como fútbol 11 los grupos antiguos de FS sin código."""
    with ops.engine.begin() as c:
        season = c.execute(text("SELECT id FROM seasons WHERE external_id = '22'")).scalar()
        cid = c.execute(text("INSERT INTO competitions (source, external_id, season_id, name, modality) "
                             "VALUES ('rffm', 'FS-ANTIGUA', :s, 'TERCERA DIVISION FUTSAL', 'futsal') RETURNING id"), {"s": season}).scalar()
        c.execute(text("INSERT INTO competition_groups (source, external_id, competition_id, name) "
                       "VALUES ('rffm', 'G-FS-ANTIGUO', :c, 'GRUPO 1')"), {"c": cid})
    with Session(ops.engine) as s:
        state = gather_state(s, "rffm", "22", "1", load_schedule())
    by_group: dict[str, Any] = {g.group: g for g in state.groups}
    assert by_group["G-FS-ANTIGUO"].game_type == "3"
    assert {g.game_type for g in state.groups if g.group != "G-FS-ANTIGUO"} == {"1"}


def test_api_never_serves_names_of_juvenile_f11_scorers(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defensa en profundidad: aunque existiera una tabla de goleadores juvenil guardada, la API no la sirve por nombre."""
    from datetime import UTC, date, datetime

    from fastapi.testclient import TestClient
    from tests.integration.test_multisport_pg import FIX, f11_league

    from futsal.api.app import create_app
    from futsal.db.table_models import ScorerRow, ScorerSnapshot
    from futsal.importer.service import run_import

    monkeypatch.delenv(ingest_scope.SCOPE_ENV, raising=False)
    with engine.begin() as c:
        c.execute(text("TRUNCATE team_entities, clubs RESTART IDENTITY CASCADE"))
    assert run_import(engine, FIX).status == "success"  # fútbol sala (su juvenil, si lo hubiera, no cambia)
    assert run_import(engine, f11_league(tmp_path, comp="26737724", group="99000002")).status == "success"
    now = datetime(2026, 11, 21, tzinfo=UTC)
    with Session(engine) as s, s.begin():
        s.execute(text("UPDATE competitions SET name = 'PRIMERA JUVENIL', category_code = '40062' WHERE external_id = '26737724'"))
        gids = dict(s.execute(text("SELECT g.external_id, g.id FROM competition_groups g")).all())
        for ext, name in (("99000002", "MENOR DE EDAD"), ("26738245", "JUGADOR FS")):
            snap = ScorerSnapshot(group_id=gids[ext], status="ok", content_hash=f"h{ext}", row_count=1, goals_total=5,
                                  source_url="x", fetched_at=now, last_seen_at=now)
            s.add(snap)
            s.flush()
            s.add(ScorerRow(snapshot_id=snap.id, row_order=0, player_external_id="1", player_name=name, team_external_id="0",
                            team_name="EQUIPO", matches_played=3, goals=5))
    client = TestClient(create_app(engine, today=lambda: date(2026, 11, 21)))
    body = client.get(f"/groups/{gids['99000002']}/scorer-table", params={"sport": "football11"}).json()
    assert body["status"] == "restricted" and body["scorers"] == [] and "MENOR" not in json.dumps(body)
    ranking = client.get("/v2/rankings/scorers", params={"sport": "football11"}).json()
    assert "MENOR" not in json.dumps(ranking)
    fs = client.get(f"/groups/{gids['26738245']}/scorer-table", params={"sport": "futsal"}).json()
    assert fs["status"] == "ok" and fs["scorers"][0]["player"] == "JUGADOR FS"  # fútbol sala, sin cambios
