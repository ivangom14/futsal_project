import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from futsal.config.settings import database_url

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine]:
    """Base PostgreSQL temporal en el servidor de `docker compose up -d db`."""
    try:
        admin_url = make_url(database_url())
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL no disponible: {exc}")
    name = f"futsal_test_{uuid.uuid4().hex[:10]}"
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = admin_url.set(database=name).render_as_string(hide_password=False)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture()
def engine(pg_engine: Engine) -> Engine:
    with pg_engine.begin() as c:
        c.execute(text("TRUNCATE seasons, competitions, competition_groups, rounds, teams, "
                       "matches, match_observations, ingestion_runs, data_quality_issues "
                       "RESTART IDENTITY CASCADE"))
    return pg_engine
