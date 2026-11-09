import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from synth_snapshot import AS_OF, SYNTHETIC_ID, build_synthetic

from futsal.config.settings import database_url
from futsal.evals import snapshot as snaps

ROOT = Path(__file__).parents[2]


@dataclass
class Synthetic:
    engine: Engine
    url: str
    admin_url: str
    root: Path
    manifest: dict[str, Any]


@pytest.fixture(scope="session")
def synthetic(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Synthetic]:
    """BD sintética migrada + snapshot (volcado y manifiesto en un directorio temporal, con los datasets del repo enlazados)."""
    try:
        admin_url = make_url(database_url())
        admin = create_engine(admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL no disponible: {exc}")
    name = f"futsal_test_evals_{uuid.uuid4().hex[:8]}"
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = admin_url.set(database=name).render_as_string(hide_password=False)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    work = tmp_path_factory.mktemp("evals-root")
    (work / "evals").mkdir()
    (work / "evals" / "datasets").symlink_to(ROOT / "evals" / "datasets")
    build_synthetic(engine, tmp_path_factory.mktemp("synthetic-json"))
    manifest = snaps.create_snapshot(SYNTHETIC_ID, AS_OF, url, work, synthetic=True)
    admin_str = admin_url.render_as_string(hide_password=False)
    yield Synthetic(engine, url, admin_str, work, manifest)
    engine.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        c.execute(text(f'DROP DATABASE IF EXISTS "{snaps.db_name(SYNTHETIC_ID)}" WITH (FORCE)'))
    admin.dispose()
    if old is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = old
