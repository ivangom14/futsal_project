"""Fase 3B · cobertura de las 12 competiciones de fútbol 11 con la cola existente, sin red (catálogo y grupos reales del 20-11-2026
descargados en la Fase 2, metadatos públicos; `tests/fixtures/rffm_f11/`).

Comprueba el inventario oficial (12 competiciones, 93 grupos, 1.627 plazas), el estado oficial (inactiva, eliminatoria), los lotes
acotados por grupo, la carga sin actas y la protección de las competiciones juveniles (sin actas por política)."""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from tests.integration.test_football11_pilot_pg import GROUP, Pilot

from futsal.ingestion.queue import ingest_scope, jobs
from futsal.ingestion.queue.planner import gather_state, tick
from futsal.ingestion.queue.schedule import load_schedule

pytestmark = pytest.mark.integration
JUVENILES = {"26738130", "26737715", "26737718", "26737724", "26738323"}
INACTIVE = "26738289"  # COPA RFEF FASE AUTONÓMICA (Activa=0)
KNOCKOUT = "28733581"  # COPA DE AFICIONADOS (tipo_competicion=1, 156 inscritos en un grupo)
EXPECTED = {"26738289": (3, 10), "26738226": (1, 18), "28733581": (1, 156), "26737698": (2, 36), "26737701": (4, 72),
            "26737706": (8, 144), "26738300": (22, 342), "26738130": (1, 18), "26737715": (4, 72), "26737718": (8, 143),
            "26737724": (17, 303), "26738323": (22, 313)}


def all_twelve(tmp_path: Path) -> Path:
    raw = json.loads(ingest_scope.SCOPE_PATH.read_text(encoding="utf-8"))  # el operativo: juveniles con reports=false
    raw["sports"]["football11"]["enabled"] = True
    for e in raw["sports"]["football11"]["competitions"]:
        e["enabled"] = True
    path = tmp_path / "all12.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


@pytest.fixture()
def world(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Pilot:
    with engine.begin() as c:
        c.execute(text("TRUNCATE ingest_jobs, raw_fetches, ingest_controls, team_entities, clubs RESTART IDENTITY CASCADE"))
    monkeypatch.setenv(ingest_scope.SCOPE_ENV, str(all_twelve(tmp_path)))
    p = Pilot(engine, tmp_path)
    with p.sf() as s, s.begin():
        jobs.enqueue(s, "rffm", "discover_catalog", {"season": "22", "game_type": "1"}, priority=40, now=p.clock.now())
    run(p, kinds=["discover_catalog", "discover_groups"])
    return p


def run(p: Pilot, **kw: Any) -> Any:
    from tests.integration.test_ingest_budget_pg import run as budget_run
    return budget_run(p, **kw)


def test_inventory_matches_the_official_catalog(world: Pilot) -> None:
    rows = world.rows("SELECT c.external_id, count(g.id), sum(g.expected_teams), c.modality FROM competitions c "
                      "JOIN competition_groups g ON g.competition_id = c.id GROUP BY c.external_id, c.modality")
    assert {r[0]: (r[1], r[2]) for r in rows} == EXPECTED and {r[3] for r in rows} == {"football11"}
    assert sum(r[1] for r in rows) == 93 and sum(r[2] for r in rows) == 1627
    requests = [u for u in world.site.calls if "/api/" in u]
    assert len(requests) == 13  # 1 catálogo + 12 grupos; las otras 31 competiciones del catálogo, ni una petición


def test_official_state_and_format_are_kept(world: Pilot) -> None:
    state = dict(world.rows("SELECT external_id, active FROM competitions"))
    assert state[INACTIVE] is False and sum(1 for v in state.values() if v) == 11
    fmt = dict(world.rows("SELECT external_id, format_code FROM competitions"))
    assert fmt[KNOCKOUT] == "1" and fmt[INACTIVE] == "2"  # eliminatoria vs «grupos»: tal cual la fuente
    kinds = dict(world.rows("SELECT external_id, kind FROM competitions"))
    assert kinds[KNOCKOUT] == "cup" and kinds["26737701"] == "league"  # nunca se trata la copa como liga
    pri = world.rows(f"SELECT DISTINCT j.priority FROM ingest_jobs j WHERE j.kind = 'calendar' AND j.target->>'competition' = '{INACTIVE}'")
    active = world.rows("SELECT DISTINCT priority FROM ingest_jobs WHERE kind = 'calendar' AND target->>'competition' = '26737701'")
    assert max(p for (p,) in pri) < min(p for (p,) in active)  # la inactiva, con la prioridad baja de siempre


def test_batches_by_group_only_plan_that_group(world: Pilot) -> None:
    with world.engine.begin() as c:
        c.execute(text("UPDATE ingest_jobs SET status = 'cancelled' WHERE status = 'pending'"))
    with Session(world.engine) as s, s.begin():
        result = tick(s, "rffm", "22", "1", load_schedule(), world.clock.now(), group_ids=[GROUP],
                      exclude_kinds=("discover_catalog", "discover_groups"))
    assert {sp.target.get("group") for sp in result.specs} == {GROUP} and result.created == len(result.specs) > 0


def test_basic_load_without_reports_never_fetches_a_report(world: Pilot) -> None:
    run(world, max_requests=60, exclude_kinds=["match_report"])  # 3B: calendarios, jornadas y tablas; actas, no
    assert not [u for u in world.site.calls if "/acta-partido/" in u]
    assert world.rows(f"SELECT count(*) FROM matches m JOIN competition_groups g ON g.id = m.group_id "
                      f"WHERE g.external_id = '{GROUP}'")[0][0] > 0
    again = len(world.site.calls)
    run(world, max_requests=60, exclude_kinds=["match_report"])  # reanudar no repite lo ya descargado
    done_twice = [u for u in world.site.calls[again:] if "calendario" in u and f"grupo={GROUP}" in u]
    assert done_twice == []


def test_juvenile_competitions_are_never_planned_for_reports(world: Pilot) -> None:
    scope = ingest_scope.load_scope()
    names = dict(world.rows("SELECT external_id, name FROM competitions"))
    codes = dict(world.rows("SELECT external_id, category_code FROM competitions"))
    for cid in JUVENILES:
        assert scope.allows(  "football11", names[cid], codes[cid]) and not scope.allows_reports("football11", names[cid], codes[cid])
    with Session(world.engine) as s:
        state = gather_state(s, "rffm", "22", "1", load_schedule())
    juvenile_groups = {g.group for g in state.groups if g.competition in JUVENILES}
    assert len(juvenile_groups) == 52 and not any(g.reports_allowed for g in state.groups if g.competition in JUVENILES)
    assert all(g.reports_allowed for g in state.groups if g.competition not in JUVENILES)
    with Session(world.engine) as s:  # y el trabajador rechaza un acta juvenil encolada a mano, sin red
        some = next(iter(juvenile_groups))
        target = {"season": "22", "competition": next(c for c in JUVENILES if some in {g.group for g in state.groups if g.competition == c}),
                  "group": some, "game_type": "1", "match": "1"}
        assert "política de actas" in str(ingest_scope.gate_target(s, scope, target))


def test_coverage_report_is_read_only_and_never_claims_full_coverage_early(world: Pilot, capsys: pytest.CaptureFixture[str]) -> None:
    import argparse

    from futsal.ingest_commands import _coverage
    before = world.rows("SELECT count(*) FROM ingest_jobs")[0][0]
    assert _coverage(world.engine, argparse.Namespace(sport="football11", json=True)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["totals"]["groups"] == 93 and out["totals"]["teams_expected"] == 1627
    assert out["totals"]["groups_with_matches"] == 0 and not any(c["complete_basic"] for c in out["competitions"])  # solo catálogo
    assert world.rows("SELECT count(*) FROM ingest_jobs")[0][0] == before  # solo lectura
    assert _coverage(world.engine, argparse.Namespace(sport="futsal", json=True)) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["groups"] == 0  # el deporte no se mezcla
