"""Fase 21: protección de identidad de partidos (R1) y de partidos finalizados (R2) en la importación."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from futsal.importer.service import run_import
from futsal.repositories.league import RegressionPolicy

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures" / "league_two_rounds.json"
FINISHED = "5575697"  # finished 3-4 en la jornada 1


def _league() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def _write(tmp_path: Path, data: dict[str, Any], name: str = "v.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _edit(data: dict[str, Any], mid: str, **changes: object) -> dict[str, Any]:
    out = copy.deepcopy(data)
    for m in out["matches"]:
        if m["external_match_id"] == mid:
            m.update(changes)
    return out


def _match(engine: Engine, mid: str) -> tuple[Any, ...]:
    with engine.connect() as c:
        return tuple(c.execute(text("SELECT status, home_score, away_score, scheduled_date, group_id FROM matches "
                                    "WHERE external_id=:m"), {"m": mid}).one())


def _issues(engine: Engine, code: str) -> list[dict[str, Any]]:
    with engine.connect() as c:
        return [r[0] for r in c.execute(text("SELECT details FROM data_quality_issues WHERE code=:c ORDER BY id"), {"c": code})]


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as c:
        return int(c.scalar(text(f"SELECT count(*) FROM {table}")) or 0)


def test_identity_conflict_does_not_move_match(engine: Engine, tmp_path: Path) -> None:
    run_import(engine, FIXTURE)
    before = _match(engine, FINISHED)
    other = _league()
    other["group"] = {"external_id": "999", "name": "Otro grupo"}
    other["matches"] = [m for m in other["matches"] if m["external_match_id"] == FINISHED]
    other["matches"][0].update(home_score=9, away_score=0, venue="OTRO")
    other["rounds"] = other["rounds"][:1]
    other["rounds"][0]["matches"] = 1
    r = run_import(engine, _write(tmp_path, other))
    assert r.status == "partial"
    assert r.issue_codes["identity_conflict"] == 1
    assert _match(engine, FINISHED) == before  # ni grupo ni marcador ni nada
    detail = _issues(engine, "identity_conflict")[0]
    assert detail["incoming_group_external_id"] == "999" and detail["stored_group_external_id"] == "26738245"
    assert detail["match_external_id"] == FINISHED
    with engine.connect() as c:
        assert c.scalar(text("SELECT status FROM ingestion_runs ORDER BY id DESC LIMIT 1")) == "partial"
        assert c.scalar(text("SELECT venue FROM matches WHERE external_id=:m"), {"m": FINISHED}) != "OTRO"


def test_finished_regression_is_held_and_source_value_is_kept(engine: Engine, tmp_path: Path) -> None:
    run_import(engine, FIXTURE)
    before = _match(engine, FINISHED)
    regressed = _edit(_league(), FINISHED, status="scheduled", home_score=None, away_score=None)
    r = run_import(engine, _write(tmp_path, regressed))
    assert r.status == "partial" and r.issue_codes["finished_regression_held"] == 1
    assert r.stats["matches"]["held"] == 1
    assert _match(engine, FINISHED) == before
    d = _issues(engine, "finished_regression_held")[0]
    assert d["stored"]["status"] == "finished" and d["incoming"]["status"] == "scheduled"
    assert d["incoming"]["home_score"] is None and d["incoming_hash"] and d["closed_acta"] is False


def test_regression_confirmed_only_after_a_prior_held_run(engine: Engine, tmp_path: Path) -> None:
    run_import(engine, FIXTURE)
    regressed = _write(tmp_path, _edit(_league(), FINISHED, status="scheduled", home_score=None, away_score=None))
    now = RegressionPolicy(confirm_after_hours=0.0)
    first = run_import(engine, regressed, policy=now)
    assert first.issue_codes["finished_regression_held"] == 1  # primera vez: siempre retenida
    assert _match(engine, FINISHED)[0] == "finished"
    second = run_import(engine, regressed, policy=now)
    assert second.issue_codes["finished_regression_confirmed"] == 1
    assert _match(engine, FINISHED)[:3] == ("scheduled", None, None)
    with engine.connect() as c:  # el valor anterior se conserva en el historial
        assert c.scalar(text("SELECT count(*) FROM match_observations o JOIN matches m ON m.id=o.match_id "
                             "WHERE m.external_id=:m"), {"m": FINISHED}) == 2


def test_regression_not_confirmed_before_the_waiting_period(engine: Engine, tmp_path: Path) -> None:
    run_import(engine, FIXTURE)
    regressed = _write(tmp_path, _edit(_league(), FINISHED, status="scheduled", home_score=None, away_score=None))
    run_import(engine, regressed)
    again = run_import(engine, regressed)  # 24 h por defecto: no han pasado
    assert again.issue_codes["finished_regression_held"] == 1 and "finished_regression_confirmed" not in again.issue_codes
    assert _match(engine, FINISHED)[0] == "finished"


def test_legitimate_correction_is_applied(engine: Engine, tmp_path: Path) -> None:
    run_import(engine, FIXTURE)
    fixed = _edit(_league(), FINISHED, home_score=4, away_score=4)
    r = run_import(engine, _write(tmp_path, fixed))
    assert r.status == "success" and r.stats["matches"]["updated"] == 1
    assert _match(engine, FINISHED)[:3] == ("finished", 4, 4)
    assert r.stats["observations"]["inserted"] == 1


def test_explicit_postponement_is_applied_and_date_change_keeps_identity(engine: Engine, tmp_path: Path) -> None:
    run_import(engine, FIXTURE)
    scheduled = "5575696"
    moved = _edit(_league(), scheduled, scheduled_date="2027-01-09")
    r = run_import(engine, _write(tmp_path, moved))
    assert r.status == "success" and _count(engine, "matches") == 14
    assert str(_match(engine, scheduled)[3]) == "2027-01-09"
    pp = _edit(_league(), scheduled, status="postponed")
    run_import(engine, _write(tmp_path, pp, "pp.json"))
    assert _match(engine, scheduled)[0] == "postponed" and _count(engine, "matches") == 14


def test_dry_run_reports_guard_issues_without_persisting(engine: Engine, tmp_path: Path) -> None:
    run_import(engine, FIXTURE)
    regressed = _write(tmp_path, _edit(_league(), FINISHED, status="scheduled", home_score=None, away_score=None))
    r = run_import(engine, regressed, dry_run=True)
    assert r.issue_codes["finished_regression_held"] == 1
    assert _issues(engine, "finished_regression_held") == []
    assert _count(engine, "ingestion_runs") == 1


def test_held_review_is_pending_until_resolved(engine: Engine, tmp_path: Path) -> None:
    from sqlalchemy.orm import Session

    from futsal.refresh.freshness import group_freshness

    run_import(engine, FIXTURE)
    regressed = _write(tmp_path, _edit(_league(), FINISHED, status="scheduled", home_score=None, away_score=None))
    run_import(engine, regressed)
    with Session(engine) as s:
        gid = int(s.execute(text("SELECT id FROM competition_groups")).scalar_one())
        assert group_freshness(s, gid)["datasets"]["matches"]["detail"]["held_for_review"] == 1  # type: ignore[index]
    r = run_import(engine, FIXTURE)  # la fuente vuelve a traer el partido finalizado
    assert r.issue_codes["finished_regression_resolved"] == 1
    with Session(engine) as s:
        assert group_freshness(s, gid)["datasets"]["matches"]["detail"]["held_for_review"] == 0  # type: ignore[index]
