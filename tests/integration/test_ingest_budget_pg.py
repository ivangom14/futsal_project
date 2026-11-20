"""Fase 3A · presupuestos ESTRICTOS de la cola sobre la RFFM simulada del piloto de fútbol 11 (fixtures anonimizadas).

Reproduce el incidente de la Fase 2: con 3 actas autorizadas, el trabajador descargó 5 porque las actas que encolaba cada jornada
tenían más prioridad que las jornadas pendientes. Ahora el tope de actas no se puede superar por prioridad, lo aplazado sigue
pendiente sin perderse ni duplicarse, la política de actas del alcance actúa antes de encolar y un solo trabajador por fuente."""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text
from tests.integration.test_football11_pilot_pg import GROUP, Pilot

from futsal.ingestion.queue import ingest_scope, jobs
from futsal.ingestion.queue.worker import LOCK_NAMESPACE

pytestmark = pytest.mark.integration


def scope_file(tmp_path: Path, *, reports: bool = True) -> Path:
    raw = json.loads(ingest_scope.SCOPE_PATH.read_text(encoding="utf-8"))
    raw["sports"]["football11"]["enabled"] = True
    for e in raw["sports"]["football11"]["competitions"]:
        e["enabled"] = e["name"] == "PREFERENTE AFICIONADO"
        if e["enabled"]:
            e["groups"], e["reports"] = [GROUP], reports
    path = tmp_path / f"scope_{reports}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


@pytest.fixture()
def rig(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Pilot:
    """Catálogo y grupos descargados; calendario y tablas pendientes (como al empezar la tanda del piloto de la Fase 2)."""
    with engine.begin() as c:
        c.execute(text("TRUNCATE ingest_jobs, raw_fetches, ingest_controls, team_entities, clubs RESTART IDENTITY CASCADE"))
    monkeypatch.setenv(ingest_scope.SCOPE_ENV, str(scope_file(tmp_path)))
    p = Pilot(engine, tmp_path)
    with p.sf() as s, s.begin():
        jobs.enqueue(s, "rffm", "discover_catalog", {"season": "22", "game_type": "1"}, priority=40, now=p.clock.now())
    run(p, kinds=["discover_catalog", "discover_groups"])  # solo el descubrimiento: el calendario queda para la prueba
    with engine.begin() as c:
        c.execute(text("UPDATE ingest_jobs SET status = 'cancelled' WHERE status = 'pending'"))  # lo que encoló el descubrimiento
    p.tick()
    assert reports_fetched(p) == 0
    return p


def reports_fetched(p: Pilot) -> int:
    return sum(1 for u in p.site.calls if "/acta-partido/" in u)


def run(p: Pilot, **kw: Any) -> Any:
    import httpx

    from futsal.ingestion.queue.breaker import CircuitBreaker
    from futsal.ingestion.queue.fetch import Fetcher
    from futsal.ingestion.queue.ratelimit import RateLimiter
    from futsal.ingestion.queue.worker import run_worker
    session = p.sf()
    fetcher = Fetcher(session, "rffm", RateLimiter(p.cfg.min_interval_s, p.cfg.max_backoff_s, p.clock.mono, p.clock.sleep),
                      CircuitBreaker("rffm", p.cfg.breaker), client=httpx.Client(transport=httpx.MockTransport(p.site)),
                      max_requests=500, snapshot_dir=p.tmp_path / "snap", network_authorized=True, now=p.clock.now)
    try:
        return run_worker(p.sf, fetcher, now=p.clock.now, work_dir=p.tmp_path / "work", sleep=p.clock.sleep, config=p.cfg, **kw)
    finally:
        session.close()


def test_the_phase2_incident_is_reproduced_without_a_report_budget(rig: Pilot) -> None:
    """Sin tope de actas (comportamiento anterior) y con 25 peticiones, como en la Fase 2: las actas adelantan a las jornadas."""
    before = len(rig.site.calls)
    report = run(rig, max_requests=25)
    assert len(rig.site.calls) - before <= 25 and report.requests_by_kind["match_report"] > 3


def test_report_budget_is_strict_and_the_rest_stays_pending(rig: Pilot) -> None:
    before = len(rig.site.calls)
    report = run(rig, max_requests=25, max_reports=3)
    assert len(rig.site.calls) - before <= 25 == report.max_requests  # el tope global nunca se supera
    assert reports_fetched(rig) == 3 and report.requests_by_kind["match_report"] == 3  # nunca más de 3 actas
    assert report.deferred_kinds == ["match_report"] and report.pending_by_kind.get("match_report", 0) > 0
    pending = rig.rows("SELECT count(*), count(DISTINCT target_key) FROM ingest_jobs WHERE kind = 'match_report' AND status = 'pending'")[0]
    assert pending[0] == pending[1] > 0  # aplazadas, sin perderse ni duplicarse
    stats = rig.rows("SELECT stats FROM ingestion_runs WHERE kind = 'ingest_worker' ORDER BY id DESC LIMIT 1")[0][0]
    assert stats["budget"] == {"max_requests": 25, "max_reports": 3, "reports_used": 3} and stats["requests_by_kind"]["match_report"] == 3


def test_deferred_reports_are_taken_by_the_next_run_without_repeating_downloads(rig: Pilot) -> None:
    run(rig, max_requests=25, max_reports=3)
    first = list(rig.site.calls)
    run(rig, max_requests=200, max_reports=2)
    later = rig.site.calls[len(first):]
    assert sum(1 for u in later if "/acta-partido/" in u) <= 2
    assert not {u for u in later if "/acta-partido/" in u} & {u for u in first if "/acta-partido/" in u}  # nada repetido


def test_zero_report_budget_and_excluded_kinds_never_fetch_reports(rig: Pilot) -> None:
    run(rig, max_requests=200, max_reports=0)
    assert reports_fetched(rig) == 0
    run(rig, max_requests=200, exclude_kinds=["match_report"])
    assert reports_fetched(rig) == 0 and rig.rows("SELECT count(*) FROM ingest_jobs WHERE kind = 'match_report' AND status = 'pending'")[0][0] > 0


def test_reports_policy_blocks_before_enqueueing(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with engine.begin() as c:
        c.execute(text("TRUNCATE ingest_jobs, raw_fetches, ingest_controls, team_entities, clubs RESTART IDENTITY CASCADE"))
    monkeypatch.setenv(ingest_scope.SCOPE_ENV, str(scope_file(tmp_path, reports=False)))  # p. ej. una competición juvenil
    p = Pilot(engine, tmp_path)
    with p.sf() as s, s.begin():
        jobs.enqueue(s, "rffm", "discover_catalog", {"season": "22", "game_type": "1"}, priority=40, now=p.clock.now())
    run(p, max_requests=200)
    p.tick()
    run(p, max_requests=200)
    p.tick()  # el planificador tampoco las propone
    assert reports_fetched(p) == 0 and p.rows("SELECT count(*) FROM ingest_jobs WHERE kind = 'match_report'")[0][0] == 0
    assert p.rows("SELECT count(*) FROM matches WHERE status = 'finished'")[0][0] > 0  # resultados y tablas sí
    with p.sf() as s, s.begin():  # y si alguien la encola a mano, el trabajador la rechaza sin red
        jobs.enqueue(s, "rffm", "match_report", {"season": "22", "competition": "26737701", "group": GROUP, "game_type": "1",
                                                 "match": "5589201"}, priority=99, now=p.clock.now())
    run(p, max_requests=200)
    assert reports_fetched(p) == 0
    assert "política de actas" in p.rows("SELECT last_error FROM ingest_jobs WHERE kind = 'match_report'")[0][0]


def test_only_one_worker_per_source(rig: Pilot) -> None:
    holder = rig.engine.connect()
    holder.execute(text("SELECT pg_advisory_lock(:ns, hashtext('rffm'))"), {"ns": LOCK_NAMESPACE})
    try:
        before = len(rig.site.calls)
        report = run(rig, max_requests=50)
        assert report.stop_reason == "locked" and len(rig.site.calls) == before and report.done == 0
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:ns, hashtext('rffm'))"), {"ns": LOCK_NAMESPACE})
        holder.close()
    assert run(rig, max_requests=1).requests == 1  # liberado el candado, vuelve a trabajar


def test_total_budget_counts_real_requests_including_failures(rig: Pilot) -> None:
    before = len(rig.site.calls)
    report = run(rig, max_requests=5)  # las jornadas 1–8 responden 404 en el simulador: también cuentan
    assert len(rig.site.calls) - before == 5 == report.requests == sum(report.requests_by_kind.values())
