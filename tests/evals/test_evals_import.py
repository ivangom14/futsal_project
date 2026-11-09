"""`snapshot import`: volcado externo -> BD nueva aislada, manifiesto, copia migrada, verify y negativas (dumps sintéticos)."""

import hashlib
import json
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from futsal.evals import cli
from futsal.evals import snapshot as snaps

ROOT = Path(__file__).parents[2]
AS_OF = "2026-10-26T21:00:00"


@pytest.fixture
def dumps(synthetic: Any, tmp_path: Path) -> Iterator[Path]:
    """Directorio de trabajo; al terminar elimina toda BD futsal_eval_imp* creada por la prueba."""
    yield tmp_path
    admin = create_engine(make_url(synthetic.admin_url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        for name in c.execute(text("SELECT datname FROM pg_database WHERE datname LIKE 'futsal_eval_imp%'")).scalars().all():
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _dump(url: str, out: Path) -> Path:
    subprocess.run([snaps.pg_tool("pg_dump"), "-Fc", "--no-owner", "-f", str(out), snaps.libpq_url(url)], check=True, capture_output=True)
    return out


def _uid() -> str:
    return "imp" + uuid.uuid4().hex[:6]


def _exists(synthetic: Any, name: str) -> bool:
    admin = create_engine(make_url(synthetic.admin_url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        found = c.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).first() is not None
    admin.dispose()
    return found


def _md5(url: str) -> str:
    engine = create_engine(url)
    try:
        digests = snaps.table_digests(engine, snaps.table_columns(engine))
    finally:
        engine.dispose()
    return hashlib.md5(json.dumps(digests, sort_keys=True).encode()).hexdigest()


def _url(synthetic: Any, name: str) -> str:
    return make_url(synthetic.admin_url).set(database=name).render_as_string(hide_password=False)


def _args(synthetic: Any, root: Path, file: Path, snap_id: str, *extra: str) -> list[str]:
    return ["snapshot", "import", "--file", str(file), "--id", snap_id, "--as-of", AS_OF, "--db", synthetic.admin_url,
            "--root", str(root), *extra]


def test_import_writes_complete_manifest(synthetic: Any, dumps: Path) -> None:
    dump = _dump(synthetic.url, dumps / "ext.dump")
    snap_id = _uid()
    assert cli.main(_args(synthetic, dumps, dump, snap_id)) == 0
    m = snaps.load_manifest(snap_id, dumps)
    assert m["source"] == "external_dump" and m["synthetic"] is False
    assert m["input_sha256"] == hashlib.sha256(dump.read_bytes()).hexdigest() and m["input_bytes"] == dump.stat().st_size
    assert m["restored_db"] == f"futsal_eval_{snap_id}" and m["input_format"] == "custom"
    revisions, head = snaps.revision_tables()
    assert m["alembic_revision"] == head and m["tables_missing"] == [] and m["tables_unexpected"] == []
    assert set(m["tables_present"]) == set(revisions[head])
    assert m["extensions"]["unaccent"] and m["extensions"]["pg_trgm"]
    assert "vector" in m["extensions"] and m["kb_documents"] == ["fifa-futsal-laws-2025-26"]
    assert m["counts"]["seasons"] == 2 and m["counts"]["competitions"] == 3 and m["counts"]["groups"] == 4
    assert m["counts"]["teams"] > 0 and m["counts"]["matches"] > 0 and m["counts"]["reports"] == 1
    assert m["target_group"] == {"external_id": "26738245", "present": True}
    # misma huella que `create` sobre la misma BD, y `verify` la acepta
    assert m["fingerprint"]["sha256"] == synthetic.manifest["fingerprint"]["sha256"]
    assert cli.main(["snapshot", "verify", "--id", snap_id, "--db", _url(synthetic, m["restored_db"]), "--root", str(dumps)]) == 0


def test_import_refuses_existing_database_without_touching_it(synthetic: Any, dumps: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dump = _dump(synthetic.url, dumps / "ext.dump")
    snap_id = _uid()
    assert cli.main(_args(synthetic, dumps, dump, snap_id)) == 0
    url = _url(synthetic, f"futsal_eval_{snap_id}")
    before = _md5(url)
    assert cli.main(_args(synthetic, dumps, dump, snap_id)) == 3
    assert "ya existe" in capsys.readouterr().err
    assert _md5(url) == before and _exists(synthetic, f"futsal_eval_{snap_id}")
    # con --upgrade-copy y solo la copia existente: también se niega y no crea la principal
    other = _uid()
    admin = create_engine(make_url(synthetic.admin_url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "futsal_eval_{other}_head"'))
    admin.dispose()
    assert cli.main(_args(synthetic, dumps, dump, other, "--upgrade-copy")) == 3
    assert not _exists(synthetic, f"futsal_eval_{other}")


def test_corrupt_file_fails_and_leaves_no_database(synthetic: Any, dumps: Path) -> None:
    bad = dumps / "bad.dump"
    bad.write_bytes(b"PGDMP\x01\x0e\x00" + bytes(range(256)) * 20)
    snap_id = _uid()
    assert cli.main(_args(synthetic, dumps, bad, snap_id)) == 1
    assert not _exists(synthetic, f"futsal_eval_{snap_id}") and not snaps.manifest_path(snap_id, dumps).exists()
    # un dump válido truncado: pg_restore falla a mitad y la BD nueva se elimina
    good = _dump(synthetic.url, dumps / "ok.dump")
    cut = dumps / "cut.dump"
    cut.write_bytes(good.read_bytes()[: good.stat().st_size // 2])
    other = _uid()
    assert cli.main(_args(synthetic, dumps, cut, other)) == 1
    assert not _exists(synthetic, f"futsal_eval_{other}") and not snaps.manifest_path(other, dumps).exists()


def test_unknown_revision_fails_and_drops_the_new_database(synthetic: Any, dumps: Path) -> None:
    name = "futsal_test_evals_" + uuid.uuid4().hex[:8]
    admin = create_engine(make_url(synthetic.admin_url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        engine = create_engine(_url(synthetic, name))
        with engine.begin() as c:
            c.execute(text("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)"))
            c.execute(text("INSERT INTO alembic_version VALUES ('9999_inventada')"))
        engine.dispose()
        dump = _dump(_url(synthetic, name), dumps / "unk.dump")
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
    snap_id = _uid()
    assert cli.main(_args(synthetic, dumps, dump, snap_id)) == 1
    assert not _exists(synthetic, f"futsal_eval_{snap_id}") and not snaps.manifest_path(snap_id, dumps).exists()


def test_upgrade_copy_leaves_original_and_migrates_only_the_copy(synthetic: Any, dumps: Path) -> None:
    # volcado de una BD desechable en 0005 (con datos) para que la subida a head tenga algo que conservar
    name = "futsal_test_evals_" + uuid.uuid4().hex[:8]
    admin = create_engine(make_url(synthetic.admin_url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    src_url = _url(synthetic, name)
    try:
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", src_url.replace("%", "%%"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        command.upgrade(cfg, "0005")
        engine = create_engine(src_url)
        with engine.begin() as c:
            c.execute(text("INSERT INTO seasons (source, external_id, name) VALUES ('rffm', '22', '2026-27')"))
        engine.dispose()
        dump = _dump(src_url, dumps / "r0005.dump")
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
    snap_id = _uid()
    assert cli.main(_args(synthetic, dumps, dump, snap_id, "--upgrade-copy")) == 0
    m = snaps.load_manifest(snap_id, dumps)
    revisions, head = snaps.revision_tables()
    # un dump en 0005 queda registrado como 0005, con las tablas de 0006/0007 como ausentes frente a head
    assert m["alembic_revision"] == "0005" and m["tables_missing"] == [] and m["alembic_head"] == head and head != "0005"
    assert m["counts"]["seasons"] == 1 and m["target_group"]["present"] is False
    original, copy = f"futsal_eval_{snap_id}", f"futsal_eval_{snap_id}_head"
    assert m["upgrade_copy"]["restored_db"] == copy and m["upgrade_copy"]["previous_tables_identical"] is True
    assert m["upgrade_copy"]["alembic_revision_before"] == "0005" and m["upgrade_copy"]["alembic_revision_after"] == head
    eo, ec = create_engine(_url(synthetic, original)), create_engine(_url(synthetic, copy))
    try:
        with eo.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0005"
        with ec.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == head
        assert set(snaps.table_columns(ec)) == set(revisions[head]) and set(snaps.table_columns(eo)) == set(revisions["0005"])
        # las tablas previas son idénticas (recuentos y md5 sobre sus columnas previas)
        cols = snaps.table_columns(eo)
        assert snaps.table_digests(ec, cols) == snaps.table_digests(eo, cols) == m["table_digests"]
    finally:
        eo.dispose()
        ec.dispose()
    # la copia migrada se verifica contra su propia huella registrada; la original, contra la del manifiesto (y no se cruzan)
    assert cli.main(["snapshot", "verify", "--id", snap_id, "--db", _url(synthetic, copy), "--root", str(dumps)]) == 0
    assert m["upgrade_copy"]["fingerprint_after"]["sha256"] != m["fingerprint"]["sha256"]
    assert cli.main(["snapshot", "verify", "--id", snap_id, "--db", _url(synthetic, original), "--root", str(dumps)]) == 0


def test_verify_ok_then_fails_after_modifying_a_row(synthetic: Any, dumps: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dump = _dump(synthetic.url, dumps / "ext.dump")
    snap_id = _uid()
    assert cli.main(_args(synthetic, dumps, dump, snap_id)) == 0
    url = _url(synthetic, f"futsal_eval_{snap_id}")
    assert cli.main(["snapshot", "verify", "--id", snap_id, "--db", url, "--root", str(dumps)]) == 0
    assert "coincide" in capsys.readouterr().out
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("UPDATE matches SET home_score = home_score + 1 WHERE id = (SELECT min(id) FROM matches WHERE home_score IS NOT NULL)"))
    engine.dispose()
    assert cli.main(["snapshot", "verify", "--id", snap_id, "--db", url, "--root", str(dumps)]) == cli.EXIT_FINGERPRINT
    assert "distinta" in capsys.readouterr().out


def test_revision_tables_come_from_the_text_of_alembic_versions() -> None:
    by_rev, head = snaps.revision_tables()
    assert "ingestion_runs" in by_rev["0001"] and "match_reports" not in by_rev["0001"] and "match_reports" in by_rev["0002"]
    assert "kb_chunks" in by_rev["0004"] and "scorer_snapshots" not in by_rev["0004"] and "scorer_snapshots" in by_rev["0005"]
    assert by_rev["0005"] < by_rev[head]


def test_plain_sql_is_loaded_only_into_the_new_database(synthetic: Any, dumps: Path) -> None:
    plain = dumps / "plain.sql"
    subprocess.run([snaps.pg_tool("pg_dump"), "-Fp", "--no-owner", "-f", str(plain), snaps.libpq_url(synthetic.url)], check=True, capture_output=True)
    snap_id = _uid()
    assert cli.main(_args(synthetic, dumps, plain, snap_id)) == 0
    m = snaps.load_manifest(snap_id, dumps)
    assert m["input_format"] == "plain" and m["fingerprint"]["sha256"] == synthetic.manifest["fingerprint"]["sha256"]
    evil = dumps / "evil.sql"
    evil.write_text("-- x\n\\connect postgres\nDROP TABLE x;\n", encoding="utf-8")
    other = _uid()
    assert cli.main(_args(synthetic, dumps, evil, other)) == 1 and not _exists(synthetic, f"futsal_eval_{other}")
