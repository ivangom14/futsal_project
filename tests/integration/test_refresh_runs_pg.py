"""Fase 21: ejecuciones (recuperación de abandonadas) y migración 0006 desde el esquema anterior."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from futsal.config.settings import database_url
from futsal.db.models import IngestionRun
from futsal.refresh.runs import finish_run, recent_runs, recover_abandoned, start_run

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
NOW = datetime(2099, 1, 1, 12, 0, tzinfo=UTC)


def _old_run(engine: Engine, hours: float, status: str = "running") -> int:
    with Session(engine) as s, s.begin():
        run = IngestionRun(started_at=NOW - timedelta(hours=hours), status=status, input_path="x", kind="refresh")
        s.add(run)
        s.flush()
        return int(run.id)


def _status(engine: Engine, run_id: int) -> tuple[str, str | None]:
    with Session(engine) as s:
        r = s.get_one(IngestionRun, run_id)
        return r.status, r.error_summary


def test_only_old_running_runs_are_recovered_and_it_is_idempotent(engine: Engine) -> None:
    old, recent, done = _old_run(engine, 10), _old_run(engine, 1), _old_run(engine, 20, "success")
    dry = recover_abandoned(engine, max_age_hours=6, now=NOW, dry_run=True)
    assert [r["run_id"] for r in dry] == [old] and _status(engine, old)[0] == "running"  # el dry-run no cambia nada
    first = recover_abandoned(engine, max_age_hours=6, now=NOW)
    assert [r["run_id"] for r in first] == [old]
    status, error = _status(engine, old)
    assert status == "abandoned" and "abandonada" in (error or "")
    assert _status(engine, recent)[0] == "running" and _status(engine, done)[0] == "success"
    assert recover_abandoned(engine, max_age_hours=6, now=NOW) == []  # idempotente
    with Session(engine) as s:
        assert s.get_one(IngestionRun, old).stats["recovered"]["max_age_hours"] == 6  # type: ignore[index]


def test_a_recovered_run_is_not_overwritten_by_a_late_finish(engine: Engine) -> None:
    run_id = start_run(engine, "refresh", {"x": 1}, NOW - timedelta(hours=9))
    recover_abandoned(engine, max_age_hours=6, now=NOW)
    assert finish_run(engine, run_id, "success", {}, now=NOW) is False
    assert _status(engine, run_id)[0] == "abandoned"


def test_finish_run_closes_a_running_run_with_counters(engine: Engine) -> None:
    run_id = start_run(engine, "refresh", {"datasets": ["matches"]}, NOW)
    assert finish_run(engine, run_id, "partial", {"groups": []}, error="x", counts={"records_inserted": 3}, now=NOW)
    row = recent_runs(engine, 1)[0]
    assert (row["status"], row["inserted"], row["error"]) == ("partial", 3, "x")


# ------------------------------------------------------------------------------------------------------ migración
@pytest.fixture()
def old_schema_url() -> str:  # type: ignore[misc]
    admin = create_engine(make_url(database_url()), isolation_level="AUTOCOMMIT")
    name = f"futsal_mig_{uuid.uuid4().hex[:10]}"
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(database_url()).set(database=name).render_as_string(hide_password=False)
    yield url
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


def _cfg(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg


def test_migration_0006_keeps_historical_runs_and_does_not_close_running_ones(old_schema_url: str) -> None:
    cfg = _cfg(old_schema_url)
    command.upgrade(cfg, "0005")
    engine = create_engine(old_schema_url)
    with engine.begin() as c:
        c.execute(text("INSERT INTO ingestion_runs(source,started_at,status,input_path,rounds_processed,matches_processed,records_inserted,"
                       "records_updated,records_unchanged,observations_created,issues_created) VALUES "
                       "('rffm',now(),'running','a',0,0,0,0,0,0,0),('rffm',now(),'success','b',2,14,14,0,0,14,1),"
                       "('rffm',now(),'failed','c',0,0,0,0,0,0,0)"))
        c.execute(text("INSERT INTO data_quality_issues(ingestion_run_id,code,entity_type,message) "
                       "SELECT id,'x','match','m' FROM ingestion_runs WHERE status='success'"))
    command.upgrade(cfg, "head")
    with engine.connect() as c:
        rows = c.execute(text("SELECT status, kind, stats, records_inserted FROM ingestion_runs ORDER BY id")).all()
        assert [r[0] for r in rows] == ["running", "success", "failed"]  # nada se reclasifica automáticamente
        assert all(r[1] == "import" and r[2] is None for r in rows) and rows[1][3] == 14
        assert c.scalar(text("SELECT details FROM data_quality_issues")) is None
        c.execute(text("SELECT count(*) FROM dataset_refreshes"))
    with engine.begin() as c:  # los estados nuevos ya son válidos; uno inventado, no
        c.execute(text("INSERT INTO ingestion_runs(source,started_at,status,input_path,kind,rounds_processed,matches_processed,records_inserted,records_updated,records_unchanged,observations_created,issues_created) "
                       "VALUES ('rffm',now(),'abandoned','z','refresh',0,0,0,0,0,0,0)"))
    with pytest.raises(Exception, match="status_valid"), engine.begin() as c:
        c.execute(text("INSERT INTO ingestion_runs(source,started_at,status,input_path,rounds_processed,matches_processed,records_inserted,records_updated,records_unchanged,observations_created,issues_created) "
                       "VALUES ('rffm',now(),'inventado','z',0,0,0,0,0,0,0)"))
    assert {"dataset_refreshes"} <= set(inspect(engine).get_table_names())
    command.downgrade(cfg, "0005")  # reversible
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM ingestion_runs")) == 4
        assert c.scalar(text("SELECT status FROM ingestion_runs WHERE input_path='z'")) == "failed"
    command.upgrade(cfg, "head")
    engine.dispose()
