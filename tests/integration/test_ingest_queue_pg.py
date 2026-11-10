"""Cola de ingesta (T06a) contra PostgreSQL: migración 0008, ciclo de vida, concurrencia, breaker y fetcher. Sin red."""

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from futsal.config.settings import database_url
from futsal.db.models import IngestControl, IngestJob, RawFetch
from futsal.importer.service import run_import
from futsal.ingestion.queue import controls, jobs
from futsal.ingestion.queue.breaker import CircuitBreaker
from futsal.ingestion.queue.controls import BreakerConfig, load_config
from futsal.ingestion.queue.fetch import (
    Fetcher,
    NetworkDisabled,
    RequestBudgetExceeded,
    SourcePaused,
)
from futsal.ingestion.queue.ratelimit import RateLimiter

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "league_two_rounds.json"
NEW_TABLES = ("ingest_jobs", "raw_fetches", "ingest_controls")
T0 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
SRC = "rffm"


@pytest.fixture()
def qe(engine: Engine) -> Engine:
    with engine.begin() as c:
        c.execute(text("TRUNCATE ingest_jobs, raw_fetches, ingest_controls RESTART IDENTITY CASCADE"))
    return engine


def add(session: Session, key: str = "a", **kw: object) -> IngestJob:
    kw.setdefault("now", T0)
    return jobs.enqueue(session, SRC, "round", {"round": key}, **kw)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- migración


@pytest.fixture()
def lifecycle_db() -> Iterator[tuple[Engine, Config]]:
    admin_url = make_url(database_url())
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    name = f"futsal_test_{uuid.uuid4().hex[:10]}"
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = admin_url.set(database=name).render_as_string(hide_password=False)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    engine = create_engine(url)
    yield engine, cfg
    engine.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


def snapshot(engine: Engine, columns: dict[str, list[str]]) -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    with engine.connect() as c:
        for table, cols in columns.items():
            row = "concat_ws('~', " + ", ".join(f"coalesce({col}::text, '<null>')" for col in cols) + ")"
            n, digest = c.execute(text(
                f"SELECT count(*), md5(coalesce(string_agg({row}, '|' ORDER BY {row}), '')) FROM {table}")).one()
            out[table] = (n, digest)
    return out


def test_migration_roundtrip_keeps_previous_data_identical(lifecycle_db: tuple[Engine, Config]) -> None:
    engine, cfg = lifecycle_db
    command.upgrade(cfg, "0007")
    assert run_import(engine, FIXTURE).status == "success"
    insp = inspect(engine)
    assert not set(NEW_TABLES) & set(insp.get_table_names())
    columns = {t: [c["name"] for c in insp.get_columns(t)] for t in insp.get_table_names() if t != "alembic_version"}
    before = snapshot(engine, columns)
    assert before["matches"][0] > 0 and before["ingestion_runs"][0] == 1

    command.upgrade(cfg, "0008")
    assert snapshot(engine, columns) == before
    assert set(NEW_TABLES) <= set(inspect(engine).get_table_names())
    command.downgrade(cfg, "0007")
    assert not set(NEW_TABLES) & set(inspect(engine).get_table_names())
    assert snapshot(engine, columns) == before
    command.upgrade(cfg, "head")
    assert snapshot(engine, columns) == before


def test_checks_and_partial_unique_index(qe: Engine) -> None:
    with Session(qe) as s, s.begin():
        first = add(s)
        again = add(s, priority=5)
        assert again.id == first.id and again.priority == 5
        assert s.query(IngestJob).count() == 1
        first.status = "done"  # un trabajo terminado libera la clave
        s.flush()
        assert add(s).id != first.id
    for stmt in ("INSERT INTO ingest_jobs (source, kind, target, target_key, status) VALUES ('rffm','x','{}','z','bogus')",
                 "INSERT INTO raw_fetches (source, url, requested_at, parse_status) VALUES ('rffm','u',now(),'bogus')",
                 "INSERT INTO ingest_controls (source, opened_by) VALUES ('rffm','bogus')"):
        with pytest.raises(IntegrityError), qe.begin() as c:
            c.execute(text(stmt))
    dup = "INSERT INTO ingest_jobs (source, kind, target, target_key) VALUES ('rffm','round','{}','{\"round\":\"a\"}')"
    with pytest.raises(IntegrityError), qe.begin() as c:
        c.execute(text(dup))


# --------------------------------------------------------------------------- ciclo de vida


def test_lifecycle_complete_and_retry_and_dead_and_cancel(qe: Engine) -> None:
    with Session(qe) as s, s.begin():
        a = add(s, "a", priority=1)
        b = add(s, "b", priority=9)
        c = add(s, "c", max_attempts=2)
        got = jobs.claim(s, "w1", T0, 300)
        assert got is not None and got.id == b.id and got.status == "running" and got.lease_until == T0 + timedelta(seconds=300)
        assert jobs.complete(s, b.id, T0).status == "done"

        got = jobs.claim(s, "w1", T0, 300)
        assert got is not None and got.id == a.id
        failed = jobs.fail(s, a.id, "boom", 503, transient=True, now=T0)
        assert (failed.status, failed.attempts, failed.last_http_status) == ("failed", 1, 503)
        assert failed.not_before == T0 + timedelta(seconds=10)
        assert jobs.claim(s, "w1", T0, 300) is not None  # c (a espera)
        assert jobs.claim(s, "w1", T0 + timedelta(seconds=9), 300) is None
        again = jobs.claim(s, "w1", T0 + timedelta(seconds=10), 300)
        assert again is not None and again.id == a.id
        assert jobs.fail(s, a.id, "x", None, transient=True, now=T0).not_before == T0 + timedelta(seconds=30)
        s.expire_all()
        jobs.fail(s, c.id, "x", 500, transient=True, now=T0)
        assert jobs.fail(s, c.id, "x", 500, transient=True, now=T0).status == "dead"  # max_attempts=2

        n = add(s, "n")
        assert jobs.cancel(s, n.id, T0) and not jobs.cancel(s, n.id, T0)
        d = add(s, "d")
        assert jobs.fail(s, d.id, "schema", None, transient=False, now=T0).status == "dead"
        jobs.save_checkpoint(s, a.id, {"last": 3}, T0)
        prog = jobs.progress(s, SRC)
        assert prog["by_status"] == {"done": 1, "failed": 1, "dead": 2, "cancelled": 1} and prog["total"] == 5
        assert prog["by_kind"]["round"]["dead"] == 2
    assert jobs.backoff_s(20) == 900.0


def test_pause_blocks_claim_until_resume(qe: Engine) -> None:
    with Session(qe) as s, s.begin():
        add(s)
        controls.pause(s, SRC, "revisión", "manual", T0)
        assert controls.is_paused(s, SRC) and jobs.claim(s, "w", T0, 60) is None
        assert controls.resume(s, SRC, T0) and not controls.is_paused(s, SRC) and not controls.resume(s, SRC)
        assert jobs.claim(s, "w", T0, 60) is not None
        with pytest.raises(ValueError):
            controls.pause(s, SRC, "x", "otro")


def test_expired_lease_is_recovered_once_without_duplicates(qe: Engine) -> None:
    with Session(qe) as s, s.begin():
        job = add(s)
        claimed = jobs.claim(s, "crashed", T0, 60)
        assert claimed is not None
        assert jobs.recover_expired_leases(s, T0 + timedelta(seconds=30)) == []  # aún vigente
        assert jobs.recover_expired_leases(s, T0 + timedelta(seconds=61)) == [job.id]
        assert jobs.recover_expired_leases(s, T0 + timedelta(seconds=61)) == []
        assert (job.status, job.attempts, job.worker, job.lease_until) == ("pending", 0, None, None)
        assert add(s).id == job.id and s.query(IngestJob).count() == 1  # no se duplica
        again = jobs.claim(s, "w2", T0 + timedelta(seconds=62), 60)
        assert again is not None and again.id == job.id
        assert jobs.claim(s, "w3", T0 + timedelta(seconds=62), 60) is None  # se ejecuta una sola vez


def test_two_connections_never_claim_the_same_job(qe: Engine) -> None:
    with Session(qe) as s, s.begin():
        ids = {add(s, str(i)).id for i in range(3)}
    s1, s2 = Session(qe), Session(qe)
    try:
        got1 = [jobs.claim(s1, "w1", T0, 60), jobs.claim(s1, "w1", T0, 60)]  # sin commit: filas bloqueadas
        got2 = [jobs.claim(s2, "w2", T0, 60), jobs.claim(s2, "w2", T0, 60)]
        taken1 = {j.id for j in got1 if j}
        taken2 = {j.id for j in got2 if j}
        assert len(taken1) == 2 and len(taken2) == 1 and not taken1 & taken2 and taken1 | taken2 == ids
        s1.commit()
        s2.commit()
    finally:
        s1.close()
        s2.close()
    with Session(qe) as s:
        assert s.query(IngestJob).filter_by(status="running").count() == 3


# --------------------------------------------------------------------------- breaker y fetcher


def mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def make_fetcher(s: Session, handler: Callable[[httpx.Request], httpx.Response], tmp_path: Path, *,
                 max_requests: int = 50, authorized: bool = True, cfg: BreakerConfig | None = None) -> Fetcher:
    return Fetcher(s, SRC, RateLimiter(0, 0, sleep=lambda _s: None), CircuitBreaker(SRC, cfg), client=mock_client(handler),
                   max_requests=max_requests, snapshot_dir=tmp_path, network_authorized=authorized, now=lambda: T0)


def url(n: int) -> str:
    return f"https://www.rffm.es/api/results?idGroup=1&round={n}"


def running_job(s: Session, key: str = "k") -> IngestJob:
    add(s, key)
    job = jobs.claim(s, "w", T0, 300)
    assert job is not None
    return job


def ok(_r: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"results": [1]})


def test_fetcher_records_raw_fetches_and_snapshot(qe: Engine, tmp_path: Path) -> None:
    with Session(qe) as s, s.begin():
        job = running_job(s)
        f = make_fetcher(s, ok, tmp_path)
        r = f.get(url(1), job.id)
        assert r.parse_status == "ok" and r.status_code == 200 and r.tripped is None
        row = s.execute(select(RawFetch)).scalar_one()
        assert (row.job_id, row.method, row.source, row.bytes, row.parse_status) == (job.id, "GET", SRC, len(r.body), "ok")
        assert row.snapshot_path == str(tmp_path / "2026-11-10" / f"{row.sha256}.json")
        assert Path(row.snapshot_path).read_bytes() == r.body and row.elapsed_ms is not None
        f.report_parse(r, "schema_error", "falta results")
        assert s.execute(select(RawFetch.parse_status)).scalar_one() == "schema_error"


def test_fetcher_budget_hosts_and_network_disabled(qe: Engine, tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, json={"a": 1})

    with Session(qe) as s, s.begin():
        f = make_fetcher(s, handler, tmp_path, max_requests=2)
        f.get(url(1))
        f.get(url(2))
        with pytest.raises(RequestBudgetExceeded):
            f.get(url(3))
        with pytest.raises(ValueError):
            f.get("https://example.com/x")
        assert len(calls) == 2 and s.query(RawFetch).count() == 2
        off = make_fetcher(s, handler, tmp_path, authorized=False)
        with pytest.raises(NetworkDisabled):
            off.get(url(9))
        controls.pause(s, SRC, "manual", "manual")
        with pytest.raises(SourcePaused):
            make_fetcher(s, handler, tmp_path).get(url(9))
        assert len(calls) == 2 and Fetcher(s, SRC, RateLimiter(), CircuitBreaker(SRC)).network_authorized is False


def trip_case(name: str) -> tuple[Callable[[httpx.Request], httpx.Response], list[int]]:
    if name == "429":
        return (lambda r: httpx.Response(429)), [1]
    if name == "403":
        return (lambda r: httpx.Response(403)), [1]
    if name == "5xx":
        return (lambda r: httpx.Response(502)), [1, 2, 3]
    if name == "network":
        def boom(r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("sin red")
        return boom, [1, 2, 3]
    return (lambda r: httpx.Response(200, content=b"null")), [1, 2]


@pytest.mark.parametrize("case", ["429", "403", "5xx", "network", "null"])
def test_breaker_transport_triggers_pause_source_and_release_job(qe: Engine, tmp_path: Path, case: str) -> None:
    handler, urls = trip_case(case)
    with Session(qe) as s, s.begin():
        job = running_job(s)
        f = make_fetcher(s, handler, tmp_path)
        results = [f.get(url(n), job.id) for n in urls]
        assert all(r.tripped is None for r in results[:-1]) and results[-1].tripped
        assert controls.is_paused(s, SRC)
        control = s.get(IngestControl, SRC)
        assert control is not None and control.opened_by == "breaker" and control.reason
        assert (job.status, job.attempts, job.worker) == ("pending", 0, None)
        assert jobs.claim(s, "w", T0, 60) is None


def test_breaker_null_bodies_need_distinct_urls_and_streaks_reset(qe: Engine, tmp_path: Path) -> None:
    with Session(qe) as s, s.begin():
        f = make_fetcher(s, lambda r: httpx.Response(200, content=b""), tmp_path)
        assert f.get(url(1)).tripped is None and f.get(url(1)).tripped is None  # misma URL: no cuenta
        assert not controls.is_paused(s, SRC)
        cb = CircuitBreaker(SRC)
        assert cb.on_response(s, url="a", status_code=502, parse_status="http_error") is None
        assert cb.on_response(s, url="b", status_code=200, parse_status="ok") is None  # rompe la racha
        assert cb.on_response(s, url="c", status_code=502, parse_status="http_error") is None
        assert not controls.is_paused(s, SRC)


def test_breaker_bad_shape_ratio_over_window(qe: Engine, tmp_path: Path) -> None:
    with Session(qe) as s, s.begin():
        job = running_job(s)
        f = make_fetcher(s, ok, tmp_path)
        results = [f.get(url(n), job.id) for n in range(1, 7)]
        for r in results[:4]:
            assert f.report_parse(r, "ok", job_id=job.id) is None
        assert f.report_parse(results[4], "schema_error", job_id=job.id) is None  # 1/5 = 20 %: no supera el umbral
        assert not controls.is_paused(s, SRC)
        assert f.report_parse(results[5], "schema_error", job_id=job.id)  # 2/6 > 20 %
        assert controls.is_paused(s, SRC) and job.status == "pending" and job.attempts == 0


def test_breaker_single_scope_mismatch_opens_immediately(qe: Engine, tmp_path: Path) -> None:
    with Session(qe) as s, s.begin():
        job = running_job(s)
        f = make_fetcher(s, ok, tmp_path)
        r = f.get(url(1), job.id)
        assert f.report_parse(r, "scope_mismatch", job_id=job.id)  # 1 de 1: sin ventana mínima ni umbral
        control = controls.get_control(s, SRC)
        assert control is not None and control.paused and control.opened_by == "breaker"
        assert "ámbito" in (control.reason or "") and job.status == "pending" and job.attempts == 0


def test_shipped_config_matches_contract() -> None:
    cfg = load_config()
    assert (cfg.min_interval_s, cfg.max_backoff_s, cfg.max_attempts, cfg.lease_s, cfg.max_requests_per_run) == (3, 60, 4, 300, 500)
    assert cfg.breaker.open_on_status == (403, 429) and cfg.breaker.bad_shape_ratio == 0.2 and cfg.breaker.min_window == 5
    assert (cfg.retry_base_s, cfg.retry_factor, cfg.retry_cap_s) == (10, 3, 900)
