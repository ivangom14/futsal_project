"""CLI `python -m futsal.evals`: subcomandos, códigos de salida y negativas."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from world import ScriptedLLM, build_world, oracle_script

from futsal.evals import cli
from futsal.evals import snapshot as snaps
from futsal.evals.runner import RunConfig, run_eval

SNAP = "synthetic-dev"
ROOT = Path(__file__).parents[2]


def test_run_requires_explicit_live_confirmation(synthetic: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["run", "--suite", "smoke40", "--snapshot", SNAP, "--max-requests", "10", "--output", str(tmp_path / "o"),
                   "--root", str(synthetic.root), "--db", synthetic.url])
    assert rc == 1 and "--confirm-live" in capsys.readouterr().err and not (tmp_path / "o").exists()


def test_cli_refuses_when_fingerprint_does_not_match(synthetic: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    restored = snaps.restore_snapshot(SNAP, synthetic.admin_url, synthetic.root)
    engine = create_engine(restored.url)
    with engine.begin() as c:
        c.execute(text("DELETE FROM match_events"))
        c.execute(text("UPDATE teams SET name = name || ' ' WHERE id = (SELECT min(id) FROM teams)"))
        c.execute(text("INSERT INTO players (source, external_id, display_name) VALUES ('rffm', 'X1', 'EXTRA PLAYER')"))
    engine.dispose()
    assert cli.main(["snapshot", "verify", "--id", SNAP, "--db", restored.url, "--root", str(synthetic.root)]) == cli.EXIT_FINGERPRINT
    assert "distinta" in capsys.readouterr().out
    rc = cli.main(["run", "--suite", "smoke40", "--snapshot", SNAP, "--max-requests", "10", "--output", str(tmp_path / "o"),
                   "--root", str(synthetic.root), "--db", restored.url, "--confirm-live"])
    assert rc == cli.EXIT_FINGERPRINT and "NO SE EJECUTA" in capsys.readouterr().err and not (tmp_path / "o").exists()
    # restaurar de nuevo deja la BD desechable idéntica al snapshot
    again = snaps.restore_snapshot(SNAP, synthetic.admin_url, synthetic.root)
    assert again.ok
    assert cli.main(["snapshot", "verify", "--id", SNAP, "--db", again.url, "--root", str(synthetic.root)]) == 0


def test_cli_run_with_a_tampered_holdout_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dst = tmp_path / "evals" / "datasets" / "futsito-v1"
    shutil.copytree(ROOT / "evals" / "datasets" / "futsito-v1", dst)
    f = dst / "s8_estadisticas.holdout.yaml"
    f.write_text(f.read_text(encoding="utf-8") + "\n# retocado\n", encoding="utf-8")
    rc = cli.main(["run", "--suite", "futsito-v1", "--split", "holdout", "--snapshot", SNAP, "--max-requests", "1", "--output", str(tmp_path / "o"),
                   "--root", str(tmp_path), "--confirm-live"])
    assert rc == 1 and "holdout no íntegro" in capsys.readouterr().err


def test_report_regenerates_from_results_json(synthetic: Any, tmp_path: Path) -> None:
    w = build_world(synthetic)
    cfg = RunConfig(suite="smoke40", snapshot=SNAP, output=tmp_path / "r", root=synthetic.root, db_url=synthetic.url, max_requests=500)
    run_eval(cfg, lambda: ScriptedLLM(oracle_script(w.resolved)))
    report = tmp_path / "r" / "report.md"
    report.unlink()
    assert cli.main(["report", str(tmp_path / "r")]) == 0 and "Informe Futsito-Eval v1" in report.read_text(encoding="utf-8")
    data = json.loads((tmp_path / "r" / "results.json").read_text(encoding="utf-8"))
    assert data["meta"]["split"] == "dev" and data["summary"]["global"]["turns"] == len(data["records"])


def test_module_entry_point_and_snapshot_create_restore(synthetic: Any, tmp_path: Path) -> None:
    proc = subprocess.run([sys.executable, "-m", "futsal.evals", "plan", "--suite", "smoke40", "--root", str(ROOT)], capture_output=True, text=True,
                          check=False, cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0 and "SIN llamadas externas" in proc.stdout, proc.stderr
    # create -> manifiesto + volcado; restore -> BD desechable verificada
    root = tmp_path / "root"
    root.mkdir()
    assert cli.main(["snapshot", "create", "--id", "cli-test", "--as-of", "2026-10-20T12:00:00", "--db", synthetic.url, "--synthetic",
                     "--root", str(root)]) == 0
    manifest = snaps.load_manifest("cli-test", root)
    assert manifest["fingerprint"]["sha256"] == synthetic.manifest["fingerprint"]["sha256"] and manifest["synthetic"] is True
    assert (root / "data" / "eval-snapshots" / "cli-test.dump").exists()
    assert cli.main(["snapshot", "restore", "--id", "cli-test", "--db", synthetic.admin_url, "--root", str(root)]) == 0
    snaps.drop_restored("cli-test", synthetic.admin_url)
