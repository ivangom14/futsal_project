"""Fase 21: comandos `refresh`, `refresh-status`, `refresh-reconcile` y `refresh-recover` de la CLI (PostgreSQL desechable, sin red)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from futsal import cli
from futsal.db.models import IngestionRun, Match

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures" / "league_two_rounds.json"
COMP, GROUP = "26738243", "26738245"


@pytest.fixture()
def catalog(tmp_path: Path, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "catalog"
    work = base / "s22_t3"
    (work / f"c{COMP}" / f"g{GROUP}").mkdir(parents=True)
    (work / f"c{COMP}" / f"g{GROUP}" / "league.json").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    inv = {"season": {"external_id": "22", "name": "2026-27"}, "game_type": "3", "entries": [{
        "identification": "confirmed", "groups_status": "ok", "competition": {"external_id": COMP, "name": "C"},
        "groups": [{"external_id": GROUP, "name": "Grupo 2", "expected_rounds": 2, "expected_teams": 14}]}]}
    (work / "inventory.json").write_text(json.dumps(inv), encoding="utf-8")
    monkeypatch.setattr("futsal.db.session.make_engine", lambda: engine)
    return base


def _args(catalog: Path, tmp_path: Path, *extra: str) -> list[str]:
    return ["refresh", "--catalog-dir", str(catalog), "--tables-dir", str(tmp_path / "t"), "--report-dir", str(tmp_path / "r"), *extra]


def test_scope_is_mandatory_and_unknown_ids_are_errors(catalog: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(_args(catalog, tmp_path, "--offline")) == 2
    assert "indique el alcance" in capsys.readouterr().out
    assert cli.main(_args(catalog, tmp_path, "--group", "123", "--offline")) == 2
    assert "no están en el inventario" in capsys.readouterr().out
    assert cli.main(_args(catalog, tmp_path, "--group", GROUP, "--only", "partidos", "--offline")) == 2
    assert "tipo de dato desconocido" in capsys.readouterr().out


def test_plan_needs_neither_network_nor_database(catalog: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("futsal.db.session.make_engine", lambda: (_ for _ in ()).throw(AssertionError("no debe abrir la base")))
    assert cli.main(_args(catalog, tmp_path, "--group", GROUP, "--plan")) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["mode"] == "incremental" and plan["groups"] == 1 and "estimated_requests" in plan


def test_refresh_dry_run_then_real_then_status_reconcile_and_recover(catalog: Path, tmp_path: Path, engine: Engine,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    out_json = tmp_path / "report.json"
    assert cli.main(_args(catalog, tmp_path, "--group", GROUP, "--only", "matches", "--offline", "--dry-run")) == 0
    text = capsys.readouterr().out
    assert "dry-run, sin registrar" in text and "matches=success(+" in text
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Match)) == 0 and s.scalar(select(func.count()).select_from(IngestionRun)) == 0

    assert cli.main(_args(catalog, tmp_path, "--group", GROUP, "--only", "matches", "--offline", "--output", str(out_json))) == 0
    assert "Resultado: success" in capsys.readouterr().out
    saved: dict[str, Any] = json.loads(out_json.read_text(encoding="utf-8"))
    assert saved["status"] == "success" and saved["run_id"]
    assert cli.main(_args(catalog, tmp_path, "--competition", COMP, "--only", "matches", "--offline")) == 0  # segunda: sin cambios
    assert "Resultado: no_changes" in capsys.readouterr().out

    assert cli.main(["refresh-status", "--group", GROUP]) == 0
    status = capsys.readouterr().out
    assert "refresh" in status and "no_changes" in status and "matches" in status and "never" in status

    assert cli.main(["refresh-reconcile", "--catalog-dir", str(catalog), "--group", GROUP, "--report-dir", str(tmp_path / "r"),
                     "--json", str(tmp_path / "rec.json"), "--fail-on-differences"]) == 0
    assert "no se ha modificado nada" in capsys.readouterr().out
    assert json.loads((tmp_path / "rec.json").read_text(encoding="utf-8"))["read_only"] is True

    with Session(engine) as s, s.begin():
        s.add(IngestionRun(started_at=datetime.now(UTC) - timedelta(hours=30), status="running", input_path="x", kind="refresh"))
    assert cli.main(["refresh-recover", "--dry-run"]) == 0
    dry = capsys.readouterr().out
    assert "1 ejecución" in dry and "se recuperarían" in dry
    assert cli.main(["refresh-recover", "--max-age-hours", "6"]) == 0
    assert "marcadas como abandoned" in capsys.readouterr().out
    assert cli.main(["refresh-recover"]) == 0
    assert capsys.readouterr().out.startswith("0 ejecución")  # idempotente


def test_exit_code_reflects_a_failed_update(catalog: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (catalog / "s22_t3" / f"c{COMP}" / f"g{GROUP}" / "league.json").write_text("no es json", encoding="utf-8")
    assert cli.main(_args(catalog, tmp_path, "--group", GROUP, "--only", "matches", "--offline")) == 1
    assert "matches=failed" in capsys.readouterr().out
