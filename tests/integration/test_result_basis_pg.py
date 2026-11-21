"""Fase 3E · resultados administrativos y estados especiales sobre PG desechable: importación, idempotencia, API v1/v2,
«próximo partido», relleno sin red desde instantáneas y migración 0011 (subida y bajada). Fútbol sala: el fixture de siempre,
cuyos partidos normales deben salir exactamente igual que antes."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from tests.integration.test_multisport_pg import (  # noqa: F401 - fixture reutilizada
    FIX,
    f11_league,
    lifecycle,
)

from futsal.api.app import create_app
from futsal.importer.result_basis_backfill import run_result_basis_backfill
from futsal.importer.service import run_import

pytestmark = pytest.mark.integration
TODAY = date(2026, 11, 21)
def q(eng: Engine, sql: str) -> list[Any]:
    with eng.connect() as c:  # se cierra siempre: una transacción abierta bloquearía el TRUNCATE de la prueba siguiente
        return list(c.execute(text(sql)).all())


NORMAL_KEYS = {"id", "group_id", "round_id", "round_number", "home_team_id", "away_team_id", "home_team", "away_team", "date",
               "status", "home_score", "away_score", "has_report"}


def special_league(tmp_path: Path) -> Path:
    """Copia F11 del fixture con los cuatro casos reales detectados en la carga de la Fase 3E y un partido normal programado."""
    path = f11_league(tmp_path)
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    ms = data["matches"]
    ms[1].update(status="unknown", result_basis="withdrawal_home", home_score=0, away_score=3, scheduled_date="2026-11-29")  # futuro
    ms[2].update(status="unknown", result_basis="unconfirmed")
    ms[3].update(status="unknown", result_basis="resolution", home_score=0, away_score=3)
    ms[4].update(status="suspended", result_basis=None)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture()
def world(engine: Engine, tmp_path: Path) -> dict[str, Any]:
    with engine.begin() as c:
        c.execute(text("TRUNCATE team_entities, clubs RESTART IDENTITY CASCADE"))
    assert run_import(engine, FIX).status == "success"  # fútbol sala, sin tocar
    league = special_league(tmp_path)
    assert run_import(engine, league).status in ("success", "success_with_issues")
    rows = {r[0]: tuple(r[1:]) for r in q(engine, "SELECT external_id, id, status, result_basis, group_id, home_team_id, away_team_id FROM matches")}
    return {"engine": engine, "league": league, "rows": rows, "client": TestClient(create_app(engine, today=lambda: TODAY))}


def test_import_stores_the_basis_and_versions_it(world: dict[str, Any]) -> None:
    rows = world["rows"]
    assert rows["F5575697"][1:3] == ("unknown", "withdrawal_home") and rows["F5575698"][1:3] == ("unknown", "unconfirmed")
    assert rows["F5575699"][1:3] == ("unknown", "resolution") and rows["F5575700"][1:3] == ("suspended", None)
    obs = q(world["engine"], "SELECT o.result_basis FROM match_observations o JOIN matches m ON m.id = o.match_id WHERE m.external_id = 'F5575697'")
    assert obs == [("withdrawal_home",)]
    fs = q(world["engine"], "SELECT count(*) FROM matches WHERE result_basis IS NOT NULL AND external_id NOT LIKE 'F%'")[0][0]
    assert fs == 0


def test_reimport_is_idempotent_for_special_and_normal_matches(world: dict[str, Any]) -> None:
    before = q(world["engine"], "SELECT count(*) FROM match_observations")[0][0]
    assert run_import(world["engine"], world["league"]).status in ("success", "success_with_issues", "no_changes")
    assert run_import(world["engine"], FIX).status in ("success", "no_changes")
    assert q(world["engine"], "SELECT count(*) FROM match_observations")[0][0] == before


def test_api_never_presents_an_administrative_or_unconfirmed_result_as_played(world: dict[str, Any]) -> None:
    c, rows = world["client"], world["rows"]
    detail = c.get(f"/matches/{rows['F5575697'][0]}", params={"sport": "football11"}).json()
    assert detail["home_score"] == 0 and detail["away_score"] == 3  # el marcador oficial se conserva
    assert detail["played"] is False and detail["result_basis"] == "withdrawal_home" and "no se disputa" in detail["result_note"]
    assert detail["observations"][-1]["result_basis"] == "withdrawal_home"
    unconfirmed = c.get(f"/matches/{rows['F5575698'][0]}", params={"sport": "football11"}).json()
    assert unconfirmed["played"] is None and "acta sin cerrar" in unconfirmed["result_note"]
    resolution = c.get(f"/matches/{rows['F5575699'][0]}", params={"sport": "football11"}).json()
    assert resolution["played"] is False and "resolución" in resolution["result_note"]
    suspended = c.get(f"/matches/{rows['F5575700'][0]}", params={"sport": "football11"}).json()
    assert suspended["status"] == "suspended" and suspended["played"] is False


def test_withdrawn_match_is_never_the_next_match_and_never_a_finished_result(world: dict[str, Any]) -> None:
    c, (mid, _, _, gid, home, away) = world["client"], world["rows"]["F5575697"]
    for team in (home, away):
        nxt = c.get(f"/groups/{gid}/matches", params={"sport": "football11", "team_id": team, "status": "scheduled",
                                                       "order": "date_asc", "limit": 1}).json()
        assert all(m["id"] != mid for m in nxt["items"])
        last = c.get(f"/groups/{gid}/matches", params={"sport": "football11", "team_id": team, "status": "finished",
                                                        "order": "desc", "limit": 1}).json()
        assert all(m["id"] != mid for m in last["items"])


def test_v2_listing_by_date_marks_the_basis_and_explains_it_once(world: dict[str, Any]) -> None:
    body = world["client"].get("/v2/matches", params={"sport": "football11", "date_from": "2026-11-29", "date_to": "2026-11-29",
                                                      "compact": "true"}).json()
    row = next(i for i in body["items"] if i["id"] == world["rows"]["F5575697"][0])
    assert row["basis"] == "withdrawal_home" and row["played"] is False and row["score"] == "0-3"
    assert sum("withdrawal_home" in n for n in body["notes"]) == 1


def test_futsal_normal_payloads_are_byte_for_byte_as_before(world: dict[str, Any]) -> None:
    gid = world["rows"]["5575697"][3]
    items = world["client"].get(f"/groups/{gid}/matches", params={"sport": "futsal"}).json()["items"]
    assert items and all(set(m) - {"past_due"} == NORMAL_KEYS for m in items)
    day = world["client"].get("/v2/matches", params={"sport": "futsal", "date_from": "2026-09-26", "date_to": "2026-10-04",
                                                     "compact": "true"}).json()
    assert day["items"] and not any("basis" in i or "played" in i for i in day["items"])
    assert not any("withdrawal_home" in n for n in day["notes"])


def test_backfill_from_stored_snapshots_without_network(world: dict[str, Any], tmp_path: Path) -> None:
    eng = world["engine"]
    finished = q(eng, "SELECT external_id, home_score, away_score, scheduled_date FROM matches "
                                          "WHERE external_id = '5575702'")[0]
    snap = tmp_path / "snap"
    snap.mkdir()
    day = finished.scheduled_date.strftime("%d/%m/%Y")
    item = {"codacta": "5575702", "acta_cerrada": "1", "estado": "1", "motivo_estado": "", "fecha": day, "Goles_casa": str(finished.home_score),
            "Goles_visitante": str(finished.away_score), "Retirado_local": "0", "Retirado_visitante": "1", "partido_en_juego": "0",
            "ResultadoProvisional": "0", "CodEquipo_local": "1", "CodEquipo_visitante": "2"}
    stale = {**item, "codacta": "5575703", "Goles_casa": "9", "Goles_visitante": "9", "Retirado_local": "1"}
    (snap / "r.json").write_text(json.dumps({"codigo_grupo": "x", "partidos": [item, stale]}), encoding="utf-8")
    first = run_result_basis_backfill(eng, snap)
    assert first["basis_set"] == {"withdrawal_away": 1} and first["stale_skipped"] == 1 and first["observations_updated"] == 1
    assert run_result_basis_backfill(eng, snap)["basis_set"] == {}  # idempotente
    got = q(eng, "SELECT status, result_basis FROM matches WHERE external_id IN ('5575702','5575703') "
                                     "ORDER BY external_id")
    assert got == [("finished", "withdrawal_away"), ("finished", None)]  # el definitivo se anota; el obsoleto no se toca
    before = q(eng, "SELECT count(*) FROM match_observations")[0][0]
    run_import(eng, FIX)  # un fichero anterior a la 3E (sin el campo) nunca borra la base guardada ni crea observaciones
    assert q(eng, "SELECT count(*) FROM match_observations")[0][0] == before
    assert q(eng, "SELECT result_basis FROM matches WHERE external_id = '5575702'")[0][0] == "withdrawal_away"


def test_migration_0011_adds_and_removes_only_the_basis(lifecycle: Any) -> None:  # noqa: F811
    eng, cfg = lifecycle
    command.upgrade(cfg, "0010")
    cols = lambda t: {r[0] for r in q(eng, f"SELECT column_name FROM information_schema.columns WHERE table_name = '{t}'")}
    assert "result_basis" not in cols("matches")
    command.upgrade(cfg, "head")
    assert "result_basis" in cols("matches") and "result_basis" in cols("match_observations")
    check = q(eng, "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                       "WHERE conname = 'ck_matches_result_basis_valid'")[0][0]
    assert all(v in check for v in ("withdrawal_home", "withdrawal_away", "resolution", "provisional", "unconfirmed"))
    command.downgrade(cfg, "0010")
    assert "result_basis" not in cols("matches") and "result_basis" not in cols("match_observations")
    command.upgrade(cfg, "head")
