"""Planificador y trabajador de la cola (T06b) contra PostgreSQL desechable. SIN red: todo con `httpx.MockTransport` y los fixtures
sintéticos de `tests/fixtures/rffm_v2/synthetic/` (2 competiciones, 3 grupos, calendarios y /api/results coherentes entre sí).

Reloj falso: solo avanzan las esperas del limitador y lo que la prueba avance. `NOW` = martes 10-11-2026 12:00 UTC. Flujo completo:
discover_catalog (1) -> discover_groups (2) -> calendar (3) -> round_results (6: 3 jornadas x 2 grupos activos) = 12 peticiones.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import sessionmaker

from futsal import ingest_commands
from futsal.db.models import DataQualityIssue, IngestionRun, IngestJob, Match, RawFetch
from futsal.db.table_models import TableIngestAttempt
from futsal.importer.schema import LeagueFile, NamedExt
from futsal.importer.service import run_import
from futsal.ingestion.queue import controls, handlers, jobs
from futsal.ingestion.queue.breaker import CircuitBreaker
from futsal.ingestion.queue.controls import load_config
from futsal.ingestion.queue.fetch import Fetcher
from futsal.ingestion.queue.planner import ROUND_RESULTS, group_target, round_target, tick
from futsal.ingestion.queue.ratelimit import RateLimiter
from futsal.ingestion.queue.schedule import load_schedule
from futsal.ingestion.queue.worker import (
    StopFlag,
    WorkerReport,
    install_signal_handlers,
    run_worker,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
SYN = ROOT / "tests" / "fixtures" / "rffm_v2" / "synthetic"
TABLES = ROOT / "tests" / "fixtures" / "rffm_tables"
SRC = "rffm"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
CATALOG_JOB = {"season": "22", "game_type": "3"}
REPORTS = 8  # actas que /api/results da por publicadas (hay_actas=1): la web sintética responde 404 (= aún no publicada)
FULL_RUN_REQUESTS = 12 + REPORTS


class Killed(BaseException):
    """Muerte abrupta del proceso simulada (no es `Exception`: el trabajador no puede capturarla)."""


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.t, self.t0 = start, start

    def now(self) -> datetime:
        return self.t

    def mono(self) -> float:
        return (self.t - self.t0).total_seconds()

    def sleep(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)

    def advance(self, **kw: float) -> None:
        self.t += timedelta(**kw)


class Site:
    """RFFM simulada con los fixtures sintéticos. `overrides[sufijo_de_url]` sustituye la respuesta; `kill_at` mata en esa petición."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls: list[str] = []
        self.times: list[datetime] = []
        self.overrides: dict[str, Callable[[], httpx.Response]] = {}
        self.kill_at: int | None = None

    @staticmethod
    def json_response(data: bytes | str, status: int = 200) -> httpx.Response:
        content = data.encode() if isinstance(data, str) else data
        return httpx.Response(status, content=content, headers={"content-type": "application/json"})

    @staticmethod
    def html_response(data: str, status: int = 200) -> httpx.Response:
        return httpx.Response(status, content=data.encode(), headers={"content-type": "text/html; charset=utf-8"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        self.times.append(self.clock.now())
        if self.kill_at is not None and len(self.calls) == self.kill_at:
            raise Killed
        for suffix, make in self.overrides.items():
            if url.endswith(suffix):
                return make()
        q = request.url.params
        path = request.url.path
        file: Path | None = None
        if path == "/api/competitions":
            file = SYN / "api_competitions_s22_t3.json"
        elif path == "/api/groups":
            file = SYN / f"api_groups_{q['competicion']}.json"
        elif path == "/competicion/calendario":
            file = SYN / f"calendario_g{q['grupo']}.html"
        elif path == "/api/results":
            file = SYN / f"api_results_g{q['idGroup']}_r{q['round']}.json"
        if file is None or not file.exists():
            return httpx.Response(404, content=b"not found")
        body = file.read_text(encoding="utf-8")
        return self.html_response(body) if file.suffix == ".html" else self.json_response(body)


class Rig:
    def __init__(self, engine: Engine, tmp_path: Path) -> None:
        self.engine, self.tmp_path = engine, tmp_path
        self.clock = Clock()
        self.site = Site(self.clock)
        self.sf = sessionmaker(engine, expire_on_commit=False)
        self.cfg = load_config()

    def worker(self, **kw: Any) -> WorkerReport:
        """Un «proceso» nuevo del trabajador: sesión, limitador e interruptor propios, como tras un reinicio."""
        session = self.sf()
        fetcher = Fetcher(session, SRC, RateLimiter(self.cfg.min_interval_s, self.cfg.max_backoff_s, self.clock.mono, self.clock.sleep),
                          CircuitBreaker(SRC, self.cfg.breaker), client=httpx.Client(transport=httpx.MockTransport(self.site)),
                          max_requests=kw.pop("fetcher_budget", 500), snapshot_dir=self.tmp_path / "snap", network_authorized=True,
                          now=self.clock.now)
        kw.setdefault("until_empty", True)
        try:
            return run_worker(self.sf, fetcher, now=self.clock.now, work_dir=self.tmp_path / "work", sleep=self.clock.sleep,
                              config=self.cfg, **kw)
        finally:
            session.close()

    def seed_catalog(self) -> None:
        with self.sf() as s, s.begin():
            jobs.enqueue(s, SRC, "discover_catalog", CATALOG_JOB, priority=40, now=self.clock.now())

    def count(self, table: str, where: str = "true") -> int:
        with self.engine.connect() as c:
            return int(c.execute(text(f"SELECT count(*) FROM {table} WHERE {where}")).scalar_one())

    def jobs_by(self) -> dict[tuple[str, str], int]:
        with self.sf() as s:
            return {(k, st): n for k, st, n in s.execute(select(IngestJob.kind, IngestJob.status, func.count())
                                                          .group_by(IngestJob.kind, IngestJob.status))}


@pytest.fixture()
def rig(engine: Engine, tmp_path: Path) -> Rig:
    with engine.begin() as c:
        c.execute(text("TRUNCATE ingest_jobs, raw_fetches, ingest_controls RESTART IDENTITY CASCADE"))
    return Rig(engine, tmp_path)


def data_snapshot(engine: Engine) -> dict[str, Any]:
    q = {
        "matches": "SELECT m.external_id, m.status, m.home_score, m.away_score, m.scheduled_at, m.scheduled_date, r.external_id, "
                   "g.external_id FROM matches m JOIN rounds r ON r.id = m.round_id JOIN competition_groups g ON g.id = m.group_id "
                   "ORDER BY 1",
        "competitions": "SELECT external_id, name, active, category_name, game_type_code, review_status FROM competitions ORDER BY 1",
        "groups": "SELECT external_id, name, expected_rounds, expected_teams, delegation, has_standings, has_scorers "
                  "FROM competition_groups ORDER BY 1",
        "rounds": "SELECT g.external_id, r.external_id, r.visible_number, r.scheduled_date FROM rounds r "
                  "JOIN competition_groups g ON g.id = r.group_id ORDER BY 1, 2",
        "teams": "SELECT g.external_id, t.external_id, t.name FROM teams t JOIN competition_groups g ON g.id = t.group_id ORDER BY 1, 2",
    }
    with engine.connect() as c:
        out: dict[str, Any] = {k: [tuple(r) for r in c.execute(text(sql))] for k, sql in q.items()}
        out["seasons"] = [tuple(r) for r in c.execute(text("SELECT external_id, name FROM seasons"))]
    return out


def full_run(rig: Rig) -> WorkerReport:
    rig.seed_catalog()
    return rig.worker()


# --------------------------------------------------------------------------- flujo completo


def test_full_pipeline_discovery_to_matches_with_hours_from_results(rig: Rig) -> None:
    report = full_run(rig)
    assert (report.stop_reason, report.requests, report.done, report.dead, report.failed) == ("empty", FULL_RUN_REQUESTS, FULL_RUN_REQUESTS, 0, 0)
    assert dict(report.by_kind) == {"discover_catalog": 1, "discover_groups": 2, "calendar": 3, "round_results": 6,
                                    "match_report": REPORTS}
    assert len(rig.site.calls) == FULL_RUN_REQUESTS and rig.count("raw_fetches", "parse_status = 'ok'") == FULL_RUN_REQUESTS - REPORTS
    snap = data_snapshot(rig.engine)
    assert len(snap["matches"]) == 12 and len(snap["groups"]) == 3 and len(snap["competitions"]) == 2 and len(snap["rounds"]) == 6
    comps = {c[0]: c for c in snap["competitions"]}
    assert comps["9000001"][2:5] == (True, "Primera Sintetica", "3") and comps["9000002"][2] is False  # metadatos oficiales (0007)
    groups = {g[0]: g for g in snap["groups"]}
    assert groups["9100001"][2:] == (3, 4, "CENTRAL R.F.F.M.", True, True)
    assert snap["seasons"] == [("22", None)]
    g1 = [m for m in snap["matches"] if m[7] == "9100001"]
    assert sorted(m[1] for m in g1) == ["finished"] * 4 + ["scheduled"] * 2
    future = sorted((m[0], m[4], m[5]) for m in g1 if m[6] == "3")
    # el calendario trae la hora vacía en la jornada 3: la hora sale de /api/results (16:00 y 18:30 de Madrid = 15:00 y 17:30 UTC)
    assert [(a.hour, a.minute) for _, a, _ in future] == [(15, 0), (17, 30)]
    assert rig.count("matches", "scheduled_at IS NULL OR scheduled_time IS NULL") == 0  # ningún partido sin hora tras /api/results


def test_full_pipeline_queue_state_checkpoints_and_run_record(rig: Rig) -> None:
    full_run(rig)
    by = rig.jobs_by()
    assert by[("discover_catalog", "done")] == 1 and by[("discover_groups", "done")] == 2 and by[("calendar", "done")] == 3
    assert by[("round_results", "done")] == 6 and by[("match_report", "done")] == REPORTS
    # los trabajos finalizados encolaron tablas agrupadas 30 min (aún no vencidas con el reloj falso)
    assert by[("standings", "pending")] == 2 and by[("scorers", "pending")] == 2
    with rig.sf() as s:
        cal = s.scalars(select(IngestJob).where(IngestJob.kind == "calendar", IngestJob.target["group"].astext == "9100001")).one()
        cp = cal.checkpoint
        assert cp and [r["code"] for r in cp["rounds"]] == ["1", "2", "3"] and cp["times_empty"] == 2 and cp["times_known"] == 4
        assert cp["rounds"][2]["matches"][0]["time"] is None and cp["rounds"][0]["matches"][0]["time"] == "16:00"
        assert cp["rounds"][0]["first_date"] == "2026-10-24" and cp["matches"] == 6
        cat = s.scalars(select(IngestJob).where(IngestJob.kind == "discover_catalog")).one()
        assert cat.checkpoint and cat.checkpoint["report"]["summary"]["competitions"] == 2
        assert [c["competition_id"] for c in cat.checkpoint["report"]["competitions"]] == ["9000001", "9000002"]
        rr = s.scalars(select(IngestJob).where(IngestJob.kind == "round_results", IngestJob.target["round"].astext == "1",
                                               IngestJob.target["group"].astext == "9100001")).one()
        assert rr.checkpoint and rr.checkpoint["new_finished"] == ["9300101", "9300102"] and rr.checkpoint["tables_enqueued"] == [
            "standings", "scorers"]
        tables = s.scalars(select(IngestJob).where(IngestJob.kind == "standings")).all()
        assert all(j.not_before == NOW + timedelta(minutes=30) or j.not_before > NOW + timedelta(minutes=29) for j in tables)
        run = s.scalars(select(IngestionRun).where(IngestionRun.kind == "ingest_worker")).one()
        assert run.status == "success" and run.stats and run.stats["requests"] == FULL_RUN_REQUESTS
        assert all(j.run_id == run.id for j in s.scalars(select(IngestJob).where(IngestJob.status == "done")))


# --------------------------------------------------------------------------- reanudación


def interrupted_run(rig: Rig, mode: str, at: int) -> tuple[WorkerReport, int]:
    """Mata el trabajador en la petición `at` (o tras `run_import` de la jornada `at`) y lo relanza tras vencer el arrendamiento."""
    rig.seed_catalog()
    table = dict(handlers.HANDLERS)
    if mode == "transport":
        rig.site.kill_at = at
    else:
        real, seen = table[ROUND_RESULTS], {"n": 0}

        def die_after_import(ctx: handlers.Context) -> dict[str, Any]:
            out = real(ctx)
            seen["n"] += 1
            if seen["n"] == at:
                raise Killed
            return out

        table[ROUND_RESULTS] = die_after_import
    with pytest.raises(Killed):
        rig.worker(handlers=table)
    with rig.sf() as s:
        running = s.scalars(select(IngestJob).where(IngestJob.status == "running")).all()
        assert len(running) == 1 and running[0].lease_until is not None  # el trabajo en curso quedó arrendado
    assert rig.count("ingestion_runs", "kind = 'ingest_worker' AND status = 'abandoned'") == 1
    rig.site.kill_at = None
    rig.clock.advance(seconds=rig.cfg.lease_s + 1)
    return rig.worker(), 1


@pytest.mark.parametrize(("mode", "at"), [("transport", 8), ("transport", 2), ("after_import", 2)])
def test_killed_worker_resumes_with_identical_data_and_no_duplicates(engine: Engine, tmp_path: Path, mode: str, at: int) -> None:
    with engine.begin() as c:
        c.execute(text("TRUNCATE ingest_jobs, raw_fetches, ingest_controls RESTART IDENTITY CASCADE"))
    base = Rig(engine, tmp_path / "base")
    full_run(base)
    expected, expected_jobs = data_snapshot(engine), base.jobs_by()

    with engine.begin() as c:
        c.execute(text("TRUNCATE seasons, competitions, competition_groups, rounds, teams, matches, match_observations, "
                       "ingestion_runs, data_quality_issues RESTART IDENTITY CASCADE"))
        c.execute(text("TRUNCATE ingest_jobs, raw_fetches, ingest_controls RESTART IDENTITY CASCADE"))
    rig = Rig(engine, tmp_path / "killed")
    report, _ = interrupted_run(rig, mode, at)
    assert report.recovered == 1 and report.stop_reason == "empty"
    assert data_snapshot(engine) == expected
    assert rig.jobs_by() == expected_jobs
    # sin duplicados: partidos únicos y como mucho un trabajo vivo por (tipo, objetivo)
    assert rig.count("matches") == len({m[0] for m in expected["matches"]}) == 12
    with rig.sf() as s:
        live = s.execute(select(IngestJob.kind, IngestJob.target_key, func.count()).where(
            IngestJob.status.in_(("pending", "running", "failed"))).group_by(IngestJob.kind, IngestJob.target_key)).all()
        assert all(n == 1 for _, _, n in live)
    # peticiones: las necesarias más, como mucho, la repetición del trabajo interrumpido
    assert rig.count("raw_fetches") <= FULL_RUN_REQUESTS + 1
    assert len(rig.site.calls) <= FULL_RUN_REQUESTS + 1


def test_max_requests_stops_the_worker_and_a_later_run_finishes_the_job_list(rig: Rig) -> None:
    rig.seed_catalog()
    first = rig.worker(max_requests=5)
    assert (first.stop_reason, first.requests) == ("max_requests", 5) and rig.count("raw_fetches") == 5
    again = rig.worker()
    assert again.stop_reason == "empty" and rig.count("raw_fetches") == FULL_RUN_REQUESTS and rig.count("matches") == 12


def test_stop_signal_finishes_the_current_job_and_takes_no_other(rig: Rig) -> None:
    rig.seed_catalog()
    flag = StopFlag()
    table = dict(handlers.HANDLERS)
    real = table["discover_catalog"]

    def stop_midway(ctx: handlers.Context) -> dict[str, Any]:
        out = real(ctx)
        flag.set()  # llega SIGTERM mientras el trabajo está en curso
        return out

    table["discover_catalog"] = stop_midway
    report = rig.worker(handlers=table, stop=flag)
    assert report.stop_reason == "signal" and report.done == 1 and rig.jobs_by()[("discover_catalog", "done")] == 1
    assert rig.jobs_by()[("discover_groups", "pending")] == 2 and not any(k[1] == "running" for k in rig.jobs_by())


def test_real_signal_handlers_set_the_flag_and_are_restored() -> None:
    flag = StopFlag()
    before = signal.getsignal(signal.SIGTERM)
    restore = install_signal_handlers(flag)
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        assert flag.is_set()
    finally:
        restore()
    assert signal.getsignal(signal.SIGTERM) == before


# --------------------------------------------------------------------------- limitación e interruptor


def test_429_opens_the_breaker_stops_the_worker_and_resume_continues(rig: Rig) -> None:
    rig.site.overrides["idGroup=9100001&round=2"] = lambda: httpx.Response(429, content=b"slow down")
    rig.seed_catalog()
    report = rig.worker()
    assert report.stop_reason == "breaker" and "429" in (report.breaker_reason or "")
    with rig.sf() as s:
        control = controls.get_control(s, SRC)
        assert control is not None and control.paused and control.opened_by == "breaker"
        blocked = s.scalars(select(IngestJob).where(IngestJob.target["group"].astext == "9100001",
                                                    IngestJob.target["round"].astext == "2", IngestJob.kind == "round_results")).one()
        assert (blocked.status, blocked.attempts) == ("pending", 0)  # un 429 nunca se reintenta ni gasta intento
    calls = len(rig.site.calls)
    stopped = rig.worker()
    assert stopped.stop_reason == "paused" and len(rig.site.calls) == calls  # en pausa: ni una petición más
    run = rig.count("ingestion_runs", "kind = 'ingest_worker' AND status = 'failed'")
    assert run == 1
    with rig.sf() as s, s.begin():
        assert controls.resume(s, SRC)  # `ingest resume`
    del rig.site.overrides["idGroup=9100001&round=2"]
    cont = rig.worker()
    assert cont.stop_reason == "empty" and rig.count("matches") == 12
    assert rig.count("raw_fetches", "status_code = 429") == 1


def test_403_opens_the_breaker_at_once(rig: Rig) -> None:
    rig.site.overrides["/api/competitions?temporada=22&tipojuego=3"] = lambda: httpx.Response(403, content=b"x")
    rig.seed_catalog()
    report = rig.worker()
    with rig.sf() as s:
        assert controls.is_paused(s, SRC)
    assert report.stop_reason == "breaker" and "403" in (report.breaker_reason or "") and len(rig.site.calls) == 1


def test_three_5xx_in_a_row_are_retried_with_backoff_and_the_third_opens_the_breaker(rig: Rig) -> None:
    rig.site.overrides["/api/competitions?temporada=22&tipojuego=3"] = lambda: httpx.Response(503, content=b"x")
    rig.seed_catalog()
    report = rig.worker(until_empty=False, poll_s=60)  # espera trabajo: el reloj falso avanza con cada sondeo
    assert report.stop_reason == "breaker" and "5xx" in (report.breaker_reason or "") and len(rig.site.calls) == 3
    gaps = [(b - a).total_seconds() for a, b in zip(rig.site.times, rig.site.times[1:], strict=False)]
    assert gaps[0] >= 10 and gaps[1] >= 30  # 10 s -> 30 s (el sondeo de 60 s los cubre)
    with rig.sf() as s:
        assert controls.is_paused(s, SRC)
        job = s.scalars(select(IngestJob)).one()
        assert (job.status, job.attempts) == ("pending", 2)  # el tercer 5xx abrió el interruptor sin consumir intento


def test_null_twice_on_different_urls_pauses_the_source(rig: Rig) -> None:
    full_run(rig)
    for rnd in ("1", "2"):
        rig.site.overrides[f"idGroup=9100001&round={rnd}"] = lambda: Site.json_response("null")
        with rig.sf() as s, s.begin():
            jobs.enqueue(s, SRC, ROUND_RESULTS, round_target(group_target("22", "9000001", "9100001", "3"), rnd), priority=40,
                         now=rig.clock.now())
    nulls_before = rig.count("raw_fetches", "parse_status = 'null_body'")
    report = rig.worker()
    assert report.stop_reason == "breaker" and "null" in (report.breaker_reason or "")
    with rig.sf() as s:
        assert controls.is_paused(s, SRC)
        states = {(j.target["round"], j.status, j.attempts) for j in s.scalars(select(IngestJob).where(
            IngestJob.kind == "round_results", IngestJob.status.in_(("pending", "failed"))))}
    assert states == {("1", "failed", 1), ("2", "pending", 0)}  # el primero se reintenta; el segundo, sin gastar intento
    assert rig.count("raw_fetches", "parse_status = 'null_body'") == nulls_before + 2


def test_a_single_null_is_a_transient_failure_retried_later_without_pausing(rig: Rig) -> None:
    hits = {"n": 0}

    def once_null() -> httpx.Response:
        hits["n"] += 1
        if hits["n"] == 1:
            return Site.json_response("null")
        return Site.json_response((SYN / "api_results_g9100001_r3.json").read_text(encoding="utf-8"))

    rig.site.overrides["idGroup=9100001&round=3"] = once_null
    rig.seed_catalog()
    first = rig.worker()
    assert first.stop_reason == "empty" and first.failed == 1
    rig.clock.advance(minutes=1)
    second = rig.worker()
    assert second.stop_reason == "empty" and rig.count("matches") == 12 and rig.jobs_by().get(("round_results", "failed")) is None


@pytest.mark.parametrize("case", ["results_group", "results_competition", "calendar_season", "calendar_group"])
def test_scope_mismatch_opens_the_breaker_with_a_single_page(rig: Rig, case: str) -> None:
    if case == "results_group":
        data = json.loads((SYN / "api_results_g9100001_r3.json").read_text(encoding="utf-8"))
        data["codigo_grupo"] = "9199999"
        rig.site.overrides["idGroup=9100001&round=3"] = lambda: Site.json_response(json.dumps(data))
    elif case == "results_competition":
        data = json.loads((SYN / "api_results_g9100001_r3.json").read_text(encoding="utf-8"))
        data["codigo_competicion"] = "9999999"
        rig.site.overrides["idGroup=9100001&round=3"] = lambda: Site.json_response(json.dumps(data))
    else:
        html = (SYN / "calendario_g9100001.html").read_text(encoding="utf-8")
        start = html.index('{"props"')
        payload = json.loads(html[start: html.index("</script>")])
        cal = payload["props"]["pageProps"]
        if case == "calendar_season":
            cal["query"]["temporada"] = "21"
            cal["calendar"]["temporada"] = "2025-2026"
        else:
            cal["query"]["grupo"] = "9199999"
        bad = html[:start] + json.dumps(payload, ensure_ascii=False) + html[html.index("</script>"):]
        rig.site.overrides["grupo=9100001&tipojuego=3"] = lambda: Site.html_response(bad)
    rig.seed_catalog()
    report = rig.worker()
    assert report.stop_reason == "breaker" and "ámbito" in (report.breaker_reason or "")
    calls = len(rig.site.calls)
    with rig.sf() as s:
        control = controls.get_control(s, SRC)
        assert control is not None and control.paused and "ámbito" in (control.reason or "")
        assert s.scalars(select(IngestJob).where(IngestJob.status == "running")).first() is None
        assert s.scalar(select(func.count()).select_from(IngestJob).where(IngestJob.attempts > 0, IngestJob.status == "dead")) == 0
        assert s.scalar(select(RawFetch.parse_status).where(RawFetch.parse_status == "scope_mismatch").limit(1)) == "scope_mismatch"
    assert rig.worker().stop_reason == "paused" and len(rig.site.calls) == calls
    if case.startswith("results"):
        assert rig.count("matches", "group_id IN (SELECT id FROM competition_groups WHERE external_id = '9100001') "
                                    "AND round_id IN (SELECT id FROM rounds WHERE external_id = '3')") == 0  # nada de otro ámbito entra


# --------------------------------------------------------------------------- ritmo


def test_requests_respect_the_minimum_interval_with_a_fake_clock(rig: Rig) -> None:
    full_run(rig)
    gaps = [(b - a).total_seconds() for a, b in zip(rig.site.times, rig.site.times[1:], strict=False)]
    assert len(rig.site.times) == FULL_RUN_REQUESTS and min(gaps) >= rig.cfg.min_interval_s == 3.0


# --------------------------------------------------------------------------- regresión (puertas de la Fase 21)


def test_a_results_page_returning_a_finished_match_as_scheduled_does_not_degrade_it(rig: Rig) -> None:
    full_run(rig)
    before = data_snapshot(rig.engine)
    held_code = "9300101"
    regressed = (SYN / "api_results_g9100001_r1_regressed.json").read_text(encoding="utf-8")
    rig.site.overrides["idGroup=9100001&round=1"] = lambda: Site.json_response(regressed)
    with rig.sf() as s, s.begin():
        jobs.enqueue(s, SRC, ROUND_RESULTS, round_target(group_target("22", "9000001", "9100001", "3"), "1"), priority=40,
                     now=rig.clock.now())
    rig.clock.advance(minutes=1)
    report = rig.worker()
    assert report.stop_reason == "empty" and report.done == 1 and report.dead == 0
    assert data_snapshot(rig.engine) == before  # el finalizado sigue finalizado, con su marcador
    with rig.sf() as s:
        issue = s.scalars(select(DataQualityIssue).where(DataQualityIssue.code == "finished_regression_held")).one()
        assert issue.external_id == held_code
        job = s.scalars(select(IngestJob).where(IngestJob.kind == ROUND_RESULTS, IngestJob.id == max(
            j for (j,) in s.execute(select(IngestJob.id).where(IngestJob.kind == ROUND_RESULTS)).all()))).one()
        assert job.checkpoint and job.checkpoint["issue_codes"] == {"finished_regression_held": 1}
        assert job.checkpoint["import_status"] == "partial" and job.checkpoint["new_finished"] == []
        assert s.scalar(select(Match.status).where(Match.external_id == held_code)) == "finished"


# --------------------------------------------------------------------------- idempotencia


def test_two_ticks_do_not_duplicate_jobs_and_plan_does_not_write(rig: Rig) -> None:
    full_run(rig)
    cfg = load_schedule()
    when = rig.clock.now() + timedelta(minutes=1)
    with rig.sf() as s, s.begin():
        first = tick(s, SRC, "22", "3", cfg, when)
    total = rig.count("ingest_jobs")
    assert first.created > 0 and total == FULL_RUN_REQUESTS + 4 + first.created  # hechos + 4 tablas encoladas + lo que encola el tick
    with rig.sf() as s, s.begin():
        second = tick(s, SRC, "22", "3", cfg, when)
    assert second.created == 0 and second.existing == len(second.specs) == len(first.specs) and rig.count("ingest_jobs") == total
    with rig.sf() as s:
        live = s.execute(select(IngestJob.kind, IngestJob.target_key, func.count()).where(
            IngestJob.status.in_(("pending", "running", "failed"))).group_by(IngestJob.kind, IngestJob.target_key)).all()
        assert all(n == 1 for _, _, n in live)


def test_two_complete_runs_leave_the_same_domain_counts(rig: Rig) -> None:
    full_run(rig)
    first = data_snapshot(rig.engine)
    first_jobs = rig.count("ingest_jobs")
    rig.clock.advance(minutes=5)  # antes de que venzan las tablas agrupadas (30 min)
    rig.seed_catalog()  # el catálogo se vuelve a descubrir
    second = rig.worker()
    assert (second.stop_reason, second.done, second.dead) == ("empty", 5, 0)  # catálogo, 2 grupos y los 2 calendarios activos
    assert data_snapshot(rig.engine) == first
    assert rig.count("ingest_jobs") > first_jobs and rig.count("matches") == 12
    assert rig.count("ingestion_runs", "kind = 'ingest_worker'") == 2


# --------------------------------------------------------------------------- tablas


def seed_tables_group(engine: Engine, tmp_path: Path) -> dict[str, Any]:
    league = LeagueFile(source="rffm", season=NamedExt(external_id="99", name=None),
                        competition=NamedExt(external_id="90000001", name="LIGA EJEMPLO FS"),
                        group=NamedExt(external_id="90000002", name="Grupo 1"), rounds=[], matches=[])
    path = tmp_path / "tables_group.json"
    path.write_text(league.model_dump_json(), encoding="utf-8")
    assert run_import(engine, path).status == "success"
    return group_target("99", "90000001", "90000002", "3")


def table_site(rig: Rig, standings: str, scorers: str = "scorers_full.html") -> None:
    rig.site.overrides["/competicion/clasificaciones?temporada=99&competicion=90000001&grupo=90000002&tipojuego=3"] = (
        lambda: Site.html_response((TABLES / standings).read_text(encoding="utf-8")))
    rig.site.overrides["/competicion/goleadores?temporada=99&competicion=90000001&grupo=90000002&tipojuego=3"] = (
        lambda: Site.html_response((TABLES / scorers).read_text(encoding="utf-8")))


def test_standings_and_scorers_jobs_reuse_table_ingest_through_the_fetcher(rig: Rig) -> None:
    target = seed_tables_group(rig.engine, rig.tmp_path)
    table_site(rig, "standings_round4_full.html")
    with rig.sf() as s, s.begin():
        for kind in ("standings", "scorers"):
            jobs.enqueue(s, SRC, kind, target, priority=60, now=rig.clock.now())
    report = rig.worker()
    assert (report.stop_reason, report.done, report.dead, report.requests) == ("empty", 2, 0, 2)
    with rig.sf() as s:
        attempts = {(a.kind, a.outcome) for a in s.scalars(select(TableIngestAttempt))}
        assert {k for k, _ in attempts} == {"standings", "scorers"} and all(o in ("ok", "incomplete") for _, o in attempts)
        assert s.scalar(select(func.count()).select_from(RawFetch).where(RawFetch.parse_status == "ok")) == 2


@pytest.mark.parametrize(("page", "expected"), [
    ("standings_null.html", ("failed", 1, False)),  # `null` en la tabla: transitorio, se reintenta (sin repetir en el acto)
    ("standings_structure_changed.html", ("dead", 1, False)),  # estructura cambiada: definitivo
    ("standings_other_group.html", ("pending", 0, True)),  # otra competición/grupo: ámbito -> interruptor
])
def test_standings_errors_are_classified(rig: Rig, page: str, expected: tuple[str, int, bool]) -> None:
    target = seed_tables_group(rig.engine, rig.tmp_path)
    table_site(rig, page)
    with rig.sf() as s, s.begin():
        jobs.enqueue(s, SRC, "standings", target, priority=60, now=rig.clock.now())
    report = rig.worker()
    with rig.sf() as s:
        job = s.scalars(select(IngestJob).where(IngestJob.kind == "standings")).one()
        assert (job.status, job.attempts, controls.is_paused(s, SRC)) == expected
    assert report.requests == 1  # null_retries=0: los reintentos los gobierna la cola, no table_ingest


# --------------------------------------------------------------------------- CLI con PostgreSQL


def test_cli_plan_is_read_only_and_tick_is_idempotent(rig: Rig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    full_run(rig)
    monkeypatch.setattr(ingest_commands, "make_engine", lambda: rig.engine)
    before = rig.count("ingest_jobs")
    at = "2026-11-10T13:00:00"  # sin zona: Europe/Madrid
    ns = argparse.Namespace(ingest_command="plan", source=SRC, season="22", game_type="3", at=at, json=True)
    assert ingest_commands.run(ns) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["jobs"] == len(out["specs"]) > 0 and rig.count("ingest_jobs") == before
    assert all({"kind", "target", "priority", "not_before", "reason"} <= s.keys() for s in out["specs"])
    ns = argparse.Namespace(ingest_command="tick", source=SRC, season="22", game_type="3", at=at)
    assert ingest_commands.run(ns) == 0
    first = capsys.readouterr().out
    after_first = rig.count("ingest_jobs")
    assert ingest_commands.run(ns) == 0
    second = capsys.readouterr().out
    assert "nuevos=0" in second and "nuevos=0" not in first and rig.count("ingest_jobs") == after_first == before + int(
        first.split("nuevos=")[1].split()[0])
    ns = argparse.Namespace(ingest_command="plan", source=SRC, season="22", game_type="3", at=at, json=False)
    assert ingest_commands.run(ns) == 0 and "trabajos" in capsys.readouterr().out


def test_cli_missing_migration_is_reported_for_every_subcommand(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from sqlalchemy import create_engine

    monkeypatch.setattr(ingest_commands, "make_engine", lambda: create_engine("sqlite://"))
    monkeypatch.setenv("FUTSAL_RFFM_NETWORK_AUTHORIZED", "1")  # las dos llaves: así `work` llega a comprobar el esquema
    base = {"source": SRC, "season": "22", "game_type": "3", "at": None, "json": False, "reason": "x", "id": 1,
            "allow_network": True, "max_requests": 1, "until_empty": True, "worker_id": "w", "work_dir": Path("x")}
    for cmd in ("status", "pause", "resume", "recover", "cancel", "plan", "tick", "work"):
        assert ingest_commands.run(argparse.Namespace(ingest_command=cmd, **base)) == 2
        assert "falta la migración 0008: alembic upgrade head" in capsys.readouterr().out


def test_module_entry_point_refuses_the_network_without_authorization() -> None:
    env = {k: v for k, v in os.environ.items() if k != "FUTSAL_RFFM_NETWORK_AUTHORIZED"}
    env["PYTHONPATH"] = str(ROOT / "src")
    done = subprocess.run([sys.executable, "-m", "futsal.ingestion.queue.worker", "--until-empty"], check=False,
                          capture_output=True, text=True, env=env)
    assert done.returncode == 2 and "rechazado" in done.stdout


def test_calendar_name_difference_is_a_quality_issue_not_a_breaker_trip(rig: Rig) -> None:
    html = (SYN / "calendario_g9100001.html").read_text(encoding="utf-8")
    renamed = html.replace("Grupo 1", "Grupo Uno").replace("PRIMERA SINTETICA FS", "PRIMERA RENOMBRADA FS")
    assert renamed != html
    rig.site.overrides["grupo=9100001&tipojuego=3"] = lambda: Site.html_response(renamed)
    full_run(rig)
    with rig.sf() as s:
        assert not controls.is_paused(s, SRC)
        job = s.scalars(select(IngestJob).where(IngestJob.kind == "calendar", IngestJob.target["group"].astext == "9100001")).one()
        assert job.status == "done" and job.checkpoint and len(job.checkpoint["name_mismatches"]) == 2
        issues = s.scalars(select(DataQualityIssue).where(DataQualityIssue.code == "name_mismatch")).all()
        assert {i.entity_type for i in issues} == {"competition", "group"}
    assert rig.count("matches") == 12
