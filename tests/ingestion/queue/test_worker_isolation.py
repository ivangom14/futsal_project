"""Independencia del planificador y del trabajador respecto del agente, y negativa a usar la red sin autorización (sin BD ni red)."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from futsal import ingest_commands
from futsal.ingestion.queue.worker import NETWORK_ENV

SRC = str(Path(__file__).parents[3] / "src")


def imported(code: str) -> list[str]:
    env = {**os.environ, "PYTHONPATH": SRC}
    done = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    return [line for line in done.stdout.splitlines() if line]


def test_planner_worker_and_handlers_do_not_import_the_agent() -> None:
    bad = imported("import sys\n"
                   "import futsal.ingestion.queue.planner, futsal.ingestion.queue.worker, futsal.ingestion.queue.handlers\n"
                   "import futsal.ingestion.queue.schedule, futsal.ingest_commands\n"
                   "print('\\n'.join(sorted(m for m in sys.modules if m == 'futsal.agent' or m.startswith('futsal.agent.'))))\n")
    assert bad == []


def test_the_agent_does_not_import_the_ingestion_queue() -> None:
    bad = imported("import sys\n"
                   "import futsal.agent.agent\n"
                   "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('futsal.ingestion.queue') "
                   "or m == 'futsal.ingest_commands')))\n")
    assert bad == []


def test_work_refuses_the_network_without_flag_or_env_before_touching_the_database(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv(NETWORK_ENV, raising=False)

    def boom() -> None:
        raise AssertionError("no debe abrir la base de datos")

    monkeypatch.setattr(ingest_commands, "make_engine", boom)
    args = argparse.Namespace(ingest_command="work", source="rffm", max_requests=1, until_empty=True, allow_network=False,
                              worker_id="w", work_dir=Path("x"))
    assert ingest_commands.run(args) == 2
    assert "rechazado" in capsys.readouterr().out


def test_plan_and_tick_arguments_exist_and_work_has_the_contract_flags() -> None:
    parser = argparse.ArgumentParser()
    ingest_commands.add_parsers(parser.add_subparsers(dest="command"))
    plan = parser.parse_args(["ingest", "plan", "--at", "2026-11-07T12:00", "--json"])
    assert plan.at == "2026-11-07T12:00" and plan.json is True
    assert parser.parse_args(["ingest", "tick"]).at is None
    work = parser.parse_args(["ingest", "work", "--max-requests", "7", "--until-empty", "--allow-network"])
    assert (work.max_requests, work.until_empty, work.allow_network) == (7, True, True)


def test_missing_migration_message_even_with_network_authorized(monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(NETWORK_ENV, "1")  # la autorización no se usa: el esquema se comprueba antes de cualquier petición
    monkeypatch.setattr(ingest_commands, "make_engine", lambda: create_engine("sqlite://"))
    args = argparse.Namespace(ingest_command="work", source="rffm", max_requests=1, until_empty=True, allow_network=True,
                              worker_id="w", work_dir=Path("x"))
    assert ingest_commands.run(args) == 2
    assert "falta la migración 0008: alembic upgrade head" in capsys.readouterr().out


@pytest.mark.parametrize(("flag", "env", "expected"), [(False, None, False), (True, None, False), (False, "1", False),
                                                      (True, "0", False), (True, "1", True)])
def test_network_requires_both_flag_and_env(monkeypatch: pytest.MonkeyPatch, flag: bool, env: str | None, expected: bool) -> None:
    """Opus 2026-11-10: una sola llave (solo --allow-network o solo la variable) NO autoriza la red real."""
    from futsal.ingestion.queue.worker import network_authorized

    if env is None:
        monkeypatch.delenv(NETWORK_ENV, raising=False)
    else:
        monkeypatch.setenv(NETWORK_ENV, env)
    assert network_authorized(flag) is expected


def test_work_with_only_the_flag_refuses_before_touching_the_database(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv(NETWORK_ENV, raising=False)

    def boom() -> None:
        raise AssertionError("no debe abrir la base de datos")

    monkeypatch.setattr(ingest_commands, "make_engine", boom)
    args = argparse.Namespace(ingest_command="work", source="rffm", max_requests=1, until_empty=True, allow_network=True,
                              worker_id="w", work_dir=Path("x"))
    assert ingest_commands.run(args) == 2
    assert "rechazado" in capsys.readouterr().out
