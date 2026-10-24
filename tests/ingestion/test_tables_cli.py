"""CLI `ingest-standings`, `ingest-scorers` y `tables-report` sin red ni PostgreSQL (--dry-run / --plan)."""
import json
from pathlib import Path

import pytest

from futsal import cli, table_commands
from helpers_tables import COMP, GROUP, FakeSite, inventory, load


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, routes: dict[tuple[str, str, str | None], object]) -> tuple[Path, Path]:
    catalog, out = tmp_path / "catalog", tmp_path / "tables"
    inv_dir = catalog / "s99_t3"
    inv_dir.mkdir(parents=True)
    (inv_dir / "inventory.json").write_text(json.dumps(inventory()), encoding="utf-8")
    site = FakeSite(routes)
    monkeypatch.setattr(table_commands, "PoliteFetcher", lambda delay=0.0, timeout=0.0: site)
    return catalog, out


def _run(*argv: str) -> int:
    return cli.main(list(argv))


def test_plan_shows_groups_and_upper_bound_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    catalog, out = _setup(tmp_path, monkeypatch, {})
    code = _run("ingest-standings", "--season", "99", "--catalog-dir", str(catalog), "--output", str(out), "--plan", "--all-rounds")
    plan = json.loads(capsys.readouterr().out)
    assert code == 0 and plan["groups"] == 1 and plan["max_requests"] == 11 and not out.exists()


def test_dry_run_ingests_each_kind_and_exit_code_reports_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                   capsys: pytest.CaptureFixture[str]) -> None:
    catalog, out = _setup(tmp_path, monkeypatch, {("clasificaciones", GROUP, None): load("standings_round4_full.html"),
                                                  ("goleadores", GROUP, None): load("scorers_structure_changed.html")})
    args = ["--season", "99", "--catalog-dir", str(catalog), "--output", str(out), "--dry-run"]
    assert _run("ingest-standings", *args) == 0
    assert f"{COMP}/{GROUP} ok jornada=4 filas=6" in capsys.readouterr().out
    assert _run("ingest-scorers", *args) == 1                                                       # cambio de estructura: no se da por buena
    printed = capsys.readouterr().out
    assert "failed" in printed and "goles" in printed
    report = json.loads((out / "s99_t3" / "tables-scorers-report.json").read_text(encoding="utf-8"))
    assert report["groups"][0]["rounds"][0]["error_code"] == "structure"


def test_missing_inventory_or_no_matching_group_is_an_environment_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                         capsys: pytest.CaptureFixture[str]) -> None:
    catalog, out = _setup(tmp_path, monkeypatch, {})
    assert _run("ingest-scorers", "--season", "77", "--catalog-dir", str(catalog), "--output", str(out), "--dry-run") == 2
    assert "discover-catalog" in capsys.readouterr().out
    assert _run("ingest-scorers", "--season", "99", "--catalog-dir", str(catalog), "--output", str(out), "--dry-run",
                "--group", "000") == 2
    assert "ningún grupo" in capsys.readouterr().out
