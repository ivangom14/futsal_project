"""Snapshot congelado de la base de datos para evaluar (docs/v2/evaluation.md §2).

- Volcado `pg_dump -Fc` en `data/eval-snapshots/<id>.dump` (fuera de Git).
- Manifiesto `evals/snapshots/<id>/manifest.json` (en Git): id, `as_of` (Europe/Madrid), sha256 del volcado, revisión de Alembic,
  documentos de la KB, recuentos y HUELLA DE CONTENIDO (sha256 de recuentos y sumas numéricas por tabla).
- `restore` crea la BD desechable `futsal_eval_<id>` y comprueba la huella; el runner se niega a ejecutar si no coincide.
"""

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

MADRID = ZoneInfo("Europe/Madrid")
PG_BIN = Path("/usr/lib/postgresql/16/bin")
DUMP_DIR = Path("data/eval-snapshots")
MANIFEST_DIR = Path("evals/snapshots")
NUMERIC_TYPES = ("smallint", "integer", "bigint", "numeric")
SCHEMA_VERSION = 1


class SnapshotError(RuntimeError):
    pass


def parse_as_of(value: str) -> datetime:
    """Instante de Europe/Madrid: un ISO sin zona se interpreta ya en Madrid; con zona se convierte."""
    moment = datetime.fromisoformat(value)
    return moment.replace(tzinfo=MADRID) if moment.tzinfo is None else moment.astimezone(MADRID)


def db_name(snap_id: str) -> str:
    return "futsal_eval_" + re.sub(r"[^a-z0-9_]", "_", snap_id.lower())


def libpq_url(sa_url: str) -> str:
    """URL de SQLAlchemy (`postgresql+psycopg://`) a URL libpq para pg_dump/pg_restore."""
    return make_url(sa_url).set(drivername="postgresql").render_as_string(hide_password=False)


def pg_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidate = PG_BIN / name
    if candidate.exists():
        return str(candidate)
    raise SnapshotError(f"no se encuentra {name} (PostgreSQL client tools)")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def fingerprint(engine: Engine) -> dict[str, Any]:
    """Huella de contenido: recuentos y sumas de las columnas numéricas exactas de cada tabla (sin marcas de tiempo)."""
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        rows = conn.execute(text(
            "SELECT table_name, column_name, data_type FROM information_schema.columns c "
            "WHERE table_schema = 'public' AND table_name <> 'alembic_version' "
            "AND table_name IN (SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
            "AND table_type = 'BASE TABLE') ORDER BY table_name, ordinal_position")).all()
        tables: dict[str, list[str]] = {}
        for table, column, dtype in rows:
            tables.setdefault(table, [])
            if dtype in NUMERIC_TYPES:
                tables[table].append(column)
        out: dict[str, Any] = {}
        for table, columns in tables.items():
            select = ", ".join(["count(*)"] + [f"COALESCE(sum({_q(c)})::numeric, 0)" for c in columns])
            values = conn.execute(text(f"SELECT {select} FROM {_q(table)}")).one()
            out[table] = {"rows": int(values[0]), "sums": {c: str(values[i + 1]) for i, c in enumerate(columns)}}
    blob = json.dumps(out, sort_keys=True, separators=(",", ":"))
    return {"sha256": hashlib.sha256(blob.encode()).hexdigest(), "tables": {t: v["rows"] for t, v in sorted(out.items())}}


def _scalar(engine: Engine, sql: str) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar()


def describe(engine: Engine) -> dict[str, Any]:
    """Revisión de Alembic, documentos indexados de la KB y recuentos principales."""
    from sqlalchemy.exc import SQLAlchemyError
    try:
        revision = _scalar(engine, "SELECT version_num FROM alembic_version")
    except SQLAlchemyError:
        revision = None
    try:
        with engine.connect() as conn:
            kb = [str(k) for k in conn.execute(text(
                "SELECT DISTINCT d.doc_key FROM kb_documents d JOIN kb_document_versions v ON v.document_id = d.id "
                "WHERE v.is_current AND v.index_status = 'indexed' "
                "AND EXISTS (SELECT 1 FROM kb_chunks c WHERE c.version_id = v.id) ORDER BY 1")).scalars()]
    except SQLAlchemyError:
        kb = []
    counts = {name: int(_scalar(engine, f"SELECT count(*) FROM {table}") or 0) for name, table in (
        ("seasons", "seasons"), ("competitions", "competitions"), ("groups", "competition_groups"), ("teams", "teams"),
        ("matches", "matches"), ("reports", "match_reports"))}
    return {"alembic_revision": revision, "kb_documents": kb, "counts": counts}


def manifest_path(snap_id: str, root: Path = Path(".")) -> Path:
    return root / MANIFEST_DIR / snap_id / "manifest.json"


def load_manifest(snap_id: str, root: Path = Path(".")) -> dict[str, Any]:
    path = manifest_path(snap_id, root)
    if not path.exists():
        raise SnapshotError(f"no existe el manifiesto {path}")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def create_snapshot(snap_id: str, as_of: str, db_url: str, root: Path = Path("."), synthetic: bool = False) -> dict[str, Any]:
    """`pg_dump -Fc` + manifiesto. Sobrescribe el snapshot si ya existe con ese id."""
    when = parse_as_of(as_of)
    dump = root / DUMP_DIR / f"{snap_id}.dump"
    dump.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([pg_tool("pg_dump"), "-Fc", "--no-owner", "--no-privileges", "-f", str(dump), libpq_url(db_url)],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SnapshotError(f"pg_dump falló: {proc.stderr.strip()[:300]}")
    engine = create_engine(db_url)
    try:
        info = describe(engine)
        fp = fingerprint(engine)
    finally:
        engine.dispose()
    version = subprocess.run([pg_tool("pg_dump"), "--version"], capture_output=True, text=True, check=False).stdout.strip()
    manifest: dict[str, Any] = {
        "schema": SCHEMA_VERSION, "id": snap_id, "synthetic": synthetic, "as_of": when.isoformat(), "timezone": "Europe/Madrid",
        "dump_file": str(DUMP_DIR / f"{snap_id}.dump"), "dump_sha256": sha256_file(dump), "pg_dump": version, **info,
        "fingerprint": fp}
    path = manifest_path(snap_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify_db(manifest: dict[str, Any], db_url: str) -> tuple[bool, str]:
    """¿La base de datos tiene exactamente el contenido del snapshot? Compara la huella de contenido."""
    engine = create_engine(db_url)
    try:
        actual = fingerprint(engine)
    finally:
        engine.dispose()
    expected = manifest["fingerprint"]["sha256"]
    if actual["sha256"] == expected:
        return True, f"huella {expected[:12]} coincide"
    migrated = (manifest.get("upgrade_copy") or {}).get("fingerprint_after", {}).get("sha256")
    if migrated and actual["sha256"] == migrated:  # la copia _head de un import --upgrade-copy: su propia huella registrada
        return True, f"huella {migrated[:12]} coincide con la de la copia migrada a head"
    diff = [t for t, n in actual["tables"].items() if n != manifest["fingerprint"]["tables"].get(t)]
    return False, (f"huella {actual['sha256'][:12]} distinta de la del snapshot {expected[:12]}"
                   + (f" (recuentos distintos en: {', '.join(diff[:6])})" if diff else " (mismos recuentos, otras sumas)"))


@dataclass
class Restored:
    url: str
    name: str
    ok: bool
    detail: str


def restore_snapshot(snap_id: str, admin_url: str, root: Path = Path(".")) -> Restored:
    """Restaura el volcado en la BD desechable `futsal_eval_<id>` (se recrea si existe) y verifica la huella."""
    manifest = load_manifest(snap_id, root)
    dump = root / manifest["dump_file"]
    if not dump.exists():
        raise SnapshotError(f"falta el volcado {dump} (los volcados no van en Git)")
    if sha256_file(dump) != manifest["dump_sha256"]:
        raise SnapshotError("el volcado no coincide con el sha256 del manifiesto")
    name = db_name(snap_id)
    admin = create_engine(make_url(admin_url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {_q(name)} WITH (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {_q(name)}"))
    finally:
        admin.dispose()
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    proc = subprocess.run([pg_tool("pg_restore"), "--no-owner", "--no-privileges", "-d", libpq_url(url), str(dump)],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SnapshotError(f"pg_restore falló: {proc.stderr.strip()[:300]}")
    ok, detail = verify_db(manifest, url)
    return Restored(url, name, ok, detail)


def drop_restored(snap_id: str, admin_url: str) -> None:
    admin = create_engine(make_url(admin_url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {_q(db_name(snap_id))} WITH (FORCE)"))
    finally:
        admin.dispose()


# --- Importación de un volcado EXTERNO (el del responsable) -------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]
HISTORIC_TARGET_GROUP = "26738245"
EXTENSIONS = ("unaccent", "pg_trgm", "vector")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_REV_RE = re.compile(r"^revision\s*(?::[^=]+)?=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
_DOWN_RE = re.compile(r"^down_revision\s*(?::[^=]+)?=\s*(?:['\"]([^'\"]+)['\"]|None)", re.MULTILINE)
_CREATE_RE = re.compile(r"op\.create_table\(\s*['\"]([A-Za-z0-9_]+)['\"]")
_DROP_RE = re.compile(r"op\.drop_table\(\s*['\"]([A-Za-z0-9_]+)['\"]")
_PLAIN_FORBIDDEN = re.compile(r"^(\\(connect|c)\b|(CREATE|DROP|ALTER)\s+DATABASE\b)", re.IGNORECASE)


class DatabaseExists(SnapshotError):
    """La BD de destino ya existe: el import nunca la toca."""


def revision_tables(versions_dir: Path | None = None) -> tuple[dict[str, frozenset[str]], str]:
    """Tablas que debe tener cada revisión de Alembic y la cabeza, deducidas del TEXTO de `alembic/versions` (sin importar los módulos)."""
    versions_dir = versions_dir or REPO_ROOT / "alembic" / "versions"
    nodes: dict[str, tuple[str | None, set[str], set[str]]] = {}
    for path in sorted(versions_dir.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        rev, down = _REV_RE.search(src), _DOWN_RE.search(src)
        if rev is None or down is None:
            continue
        upgrade = src.split("def downgrade", 1)[0]
        nodes[rev.group(1)] = (down.group(1), set(_CREATE_RE.findall(upgrade)), set(_DROP_RE.findall(upgrade)))
    children = {d for d, _, _ in nodes.values() if d}
    heads = [r for r in nodes if r not in children]
    if len(heads) != 1:
        raise SnapshotError(f"alembic/versions no tiene una única cabeza: {sorted(heads)}")
    result: dict[str, frozenset[str]] = {}

    def tables_of(rev: str) -> frozenset[str]:
        if rev not in result:
            down, created, dropped = nodes[rev]
            result[rev] = frozenset((tables_of(down) if down else frozenset()) | created) - dropped
        return result[rev]

    for r in nodes:
        tables_of(r)
    return result, heads[0]


def _detect_format(path: Path) -> str:
    """`custom|directory|tar` si `pg_restore --list` lo acepta; `plain` si es SQL de texto; si no, SnapshotError."""
    if not path.exists():
        raise SnapshotError(f"no existe el fichero {path}")
    proc = subprocess.run([pg_tool("pg_restore"), "--list", str(path)], capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        return "directory" if path.is_dir() else ("tar" if path.read_bytes()[257:262] == b"ustar" else "custom")
    if path.is_dir():
        raise SnapshotError(f"directorio que no es un volcado válido: {proc.stderr.strip()[:200]}")
    head = path.read_bytes()[:65536]
    try:
        text_head = head.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        text_head = ""
    if text_head and "\x00" not in text_head and re.search(r"^(--|SET |CREATE |COPY |BEGIN)", text_head, re.MULTILINE):
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if _PLAIN_FORBIDDEN.match(line):
                    raise SnapshotError("el SQL plano contiene \\connect o CREATE/DROP/ALTER DATABASE: se niega (solo se toca la BD nueva)")
        return "plain"
    raise SnapshotError(f"fichero ilegible o corrupto (no es custom, directory, tar ni SQL plano): {proc.stderr.strip()[:200]}")


def _input_digest(path: Path) -> tuple[str, int]:
    if not path.exists():
        raise SnapshotError(f"no existe el fichero {path}")
    if not path.is_dir():
        return sha256_file(path), path.stat().st_size
    h, size = hashlib.sha256(), 0
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(f.relative_to(path).as_posix().encode())
        h.update(sha256_file(f).encode())
        size += f.stat().st_size
    return h.hexdigest(), size


def _db_exists(admin: Engine, name: str) -> bool:
    with admin.connect() as conn:
        return conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).first() is not None


def _load_dump(path: Path, fmt: str, url: str) -> None:
    if fmt == "plain":
        cmd = [pg_tool("psql"), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-1", "-d", libpq_url(url), "-f", str(path)]
    else:
        cmd = [pg_tool("pg_restore"), "--no-owner", "--no-privileges", "--exit-on-error", "-d", libpq_url(url), str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SnapshotError(f"{Path(cmd[0]).name} falló: {proc.stderr.strip()[:300]}")


def table_columns(engine: Engine) -> dict[str, list[str]]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT c.table_name, c.column_name FROM information_schema.columns c JOIN information_schema.tables t "
            "ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
            "WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE' AND c.table_name <> 'alembic_version' "
            "ORDER BY c.table_name, c.ordinal_position")).all()
    out: dict[str, list[str]] = {}
    for table, column in rows:
        out.setdefault(table, []).append(column)
    return out


def table_digests(engine: Engine, columns: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    """Recuento y md5 del contenido de cada tabla restringido a las columnas dadas (para comparar antes y después de migrar)."""
    out: dict[str, dict[str, Any]] = {}
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        for table, cols in sorted(columns.items()):
            row = ", ".join(_q(c) for c in cols)
            value = conn.execute(text(
                f"SELECT count(*), md5(COALESCE(string_agg(r, E'\\n' ORDER BY r), '')) "
                f"FROM (SELECT ROW({row})::text AS r FROM {_q(table)}) s")).one()
            out[table] = {"rows": int(value[0]), "md5": value[1]}
    return out


def _inspect_import(engine: Engine, versions_dir: Path | None) -> dict[str, Any]:
    from sqlalchemy.exc import SQLAlchemyError
    by_rev, head = revision_tables(versions_dir)
    try:
        revision = _scalar(engine, "SELECT version_num FROM alembic_version")
    except SQLAlchemyError as exc:
        raise SnapshotError("la BD restaurada no tiene alembic_version: revisión desconocida") from exc
    if revision not in by_rev:
        raise SnapshotError(f"revisión de alembic desconocida en el volcado: {revision!r} (conocidas: {', '.join(sorted(by_rev))})")
    columns = table_columns(engine)
    present = set(columns)
    fp = fingerprint(engine)
    with engine.connect() as conn:
        extensions = {e: conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = :e"), {"e": e}).first() is not None for e in EXTENSIONS}
    try:
        with engine.connect() as conn:
            kb = [str(k) for k in conn.execute(text(
                "SELECT DISTINCT d.doc_key FROM kb_documents d JOIN kb_document_versions v ON v.document_id = d.id "
                "WHERE v.is_current AND v.index_status = 'indexed' "
                "AND EXISTS (SELECT 1 FROM kb_chunks c WHERE c.version_id = v.id) ORDER BY 1")).scalars()]
    except SQLAlchemyError:
        kb = []
    target = 0
    if "competition_groups" in present:
        with engine.connect() as conn:
            target = int(conn.execute(text("SELECT count(*) FROM competition_groups WHERE external_id = :g"),
                                      {"g": HISTORIC_TARGET_GROUP}).scalar() or 0)
    counts = {name: (int(_scalar(engine, f"SELECT count(*) FROM {table}") or 0) if table in present else 0) for name, table in (
        ("seasons", "seasons"), ("competitions", "competitions"), ("groups", "competition_groups"), ("teams", "teams"),
        ("matches", "matches"), ("reports", "match_reports"))}
    return {"alembic_revision": revision, "alembic_head": head, "tables_present": sorted(present),
            "tables_missing": sorted(by_rev[revision] - present), "tables_unexpected": sorted(present - by_rev[revision]),
            "extensions": extensions, "kb_documents": kb, "counts": counts,
            "target_group": {"external_id": HISTORIC_TARGET_GROUP, "present": target > 0},
            "fingerprint": fp, "table_digests": table_digests(engine, columns), "_columns": columns}


def _alembic_upgrade(url: str) -> None:
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    command.upgrade(cfg, "head")


def import_snapshot(file: Path, snap_id: str, as_of: str, admin_url: str, root: Path = Path("."), upgrade_copy: bool = False,
                    versions_dir: Path | None = None) -> dict[str, Any]:
    """Restaura un volcado externo en una BD NUEVA `futsal_eval_<id>` (nunca toca otra), la inspecciona y escribe el manifiesto.

    Con `upgrade_copy`, restaura el mismo fichero en `futsal_eval_<id>_head` y migra SOLO esa copia. Si algo falla se eliminan las
    BD creadas por esta llamada (y solo esas). Si alguna ya existe lanza `DatabaseExists` sin hacer nada.
    """
    if not _ID_RE.match(snap_id):
        raise SnapshotError(f"id no válido: {snap_id!r} (letras, dígitos, '-' y '_')")
    name = db_name(snap_id)
    head_name = name + "_head"
    if len(head_name) > 63:
        raise SnapshotError("id demasiado largo para un nombre de base de datos de PostgreSQL")
    when = parse_as_of(as_of)
    input_sha, input_bytes = _input_digest(file)
    fmt = _detect_format(file)
    admin = create_engine(make_url(admin_url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    created: list[str] = []
    engines: list[Engine] = []
    try:
        for candidate in (name, head_name) if upgrade_copy else (name,):
            if _db_exists(admin, candidate):
                raise DatabaseExists(f"la base de datos {candidate} ya existe: no se toca (elimínala a mano o usa otro --id)")

        def restore_into(db: str) -> str:
            with admin.connect() as conn:
                conn.execute(text(f"CREATE DATABASE {_q(db)}"))
            created.append(db)
            target_url = make_url(admin_url).set(database=db).render_as_string(hide_password=False)
            _load_dump(file, fmt, target_url)
            return target_url

        engine = create_engine(restore_into(name))
        engines.append(engine)
        info = _inspect_import(engine, versions_dir)
        columns = info.pop("_columns")
        try:
            dump_file = str(file.resolve().relative_to(root.resolve()))
        except ValueError:
            dump_file = str(file.resolve())
        manifest: dict[str, Any] = {
            "schema": SCHEMA_VERSION, "id": snap_id, "source": "external_dump", "synthetic": False, "as_of": when.isoformat(),
            "timezone": "Europe/Madrid", "input_sha256": input_sha, "input_bytes": input_bytes, "input_format": fmt,
            "dump_file": dump_file, "dump_sha256": input_sha, "restored_db": name, **info}
        if upgrade_copy:
            head_url = restore_into(head_name)
            head_engine = create_engine(head_url)
            engines.append(head_engine)
            before = fingerprint(head_engine)
            if before["sha256"] != info["fingerprint"]["sha256"]:
                raise SnapshotError("dos restauraciones del mismo fichero dan huellas distintas")
            _alembic_upgrade(head_url)
            after = table_digests(head_engine, columns)
            changed = sorted(t for t, d in info["table_digests"].items() if after.get(t) != d)
            if changed:
                raise SnapshotError(f"tras la subida a head cambian recuentos o md5 de tablas previas: {', '.join(changed)}")
            manifest["upgrade_copy"] = {
                "restored_db": head_name, "alembic_revision_before": info["alembic_revision"],
                "alembic_revision_after": _scalar(head_engine, "SELECT version_num FROM alembic_version"),
                "fingerprint_before": before, "fingerprint_after": fingerprint(head_engine), "previous_tables_identical": True,
                "previous_tables": sorted(after)}
        path = manifest_path(snap_id, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return manifest
    except BaseException:
        for e in engines:
            e.dispose()
        engines.clear()
        with admin.connect() as conn:
            for db in created:
                conn.execute(text(f"DROP DATABASE IF EXISTS {_q(db)} WITH (FORCE)"))
        raise
    finally:
        for e in engines:
            e.dispose()
        admin.dispose()
