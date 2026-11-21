"""Trabajador de la cola de ingesta (T06b): un proceso, un `source`, sin concurrencia. Independiente de `futsal.agent`.

Bucle: `recover_expired_leases` -> `claim` -> manejador -> `complete` / `fail`. Se detiene si el source está en pausa (manual o por el
interruptor), se agota el tope de peticiones, la cola no tiene nada vencido (`until_empty`) o llega SIGINT/SIGTERM (se termina el
trabajo en curso y no se toma otro). Las escrituras son idempotentes: si el proceso muere, el arrendamiento vence y otro trabajador
repite el trabajo sin duplicar nada (como mucho se repite una petición).

La red real está DESACTIVADA salvo con `--allow-network` Y `FUTSAL_RFFM_NETWORK_AUTHORIZED=1` a la vez. Punto de entrada alternativo:
`python -m futsal.ingestion.queue.worker`.
"""

import argparse
import contextlib
import os
import signal
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from futsal.db.models import IngestionRun
from futsal.ingestion.queue import controls, jobs
from futsal.ingestion.queue.breaker import CircuitBreaker, is_transient_reason
from futsal.ingestion.queue.controls import QueueConfig, load_config
from futsal.ingestion.queue.fetch import (
    Fetcher,
    NetworkDisabled,
    RequestBudgetExceeded,
    SourcePaused,
)
from futsal.ingestion.queue.handlers import (
    HANDLERS,
    BreakerTripped,
    Context,
    Handler,
    JobError,
    JobView,
)
from futsal.ingestion.queue.ingest_scope import gate_target, load_scope
from futsal.ingestion.queue.planner import MATCH_REPORT
from futsal.ingestion.queue.ratelimit import RateLimiter
from futsal.ingestion.queue.schedule import ScheduleConfig, load_schedule

NETWORK_ENV = "FUTSAL_RFFM_NETWORK_AUTHORIZED"
DEFAULT_WORK_DIR = Path("data/rffm/queue")
REQUIRED_TABLES = ("ingest_jobs", "raw_fetches", "ingest_controls")
MIGRATION_MESSAGE = "falta la migración 0008: alembic upgrade head"


class MigrationMissing(RuntimeError):
    """La base de datos no tiene las tablas de la cola (migración 0008)."""


def ensure_schema(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    if not set(REQUIRED_TABLES) <= tables:
        raise MigrationMissing(MIGRATION_MESSAGE)


class StopFlag:
    """Señal de parada cooperativa (la fija el manejador de SIGINT/SIGTERM o la prueba)."""

    def __init__(self) -> None:
        self._set = False

    def set(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set


def install_signal_handlers(flag: StopFlag) -> Callable[[], None]:
    """SIGINT/SIGTERM: la primera pide parar al terminar el trabajo en curso; la segunda interrumpe. Devuelve el restaurador."""
    previous: dict[int, Any] = {}

    def handler(signum: int, _frame: FrameType | None) -> None:
        if flag.is_set():
            raise KeyboardInterrupt
        flag.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.signal(sig, handler)

    def restore() -> None:
        for sig, old in previous.items():
            signal.signal(sig, old)

    return restore


LOCK_NAMESPACE = 4242  # espacio de los candados de PostgreSQL de la cola (un trabajador por fuente)


@dataclass
class WorkerReport:
    worker_id: str
    stop_reason: str = "empty"
    run_id: int | None = None
    requests: int = 0
    recovered: int = 0
    done: int = 0
    failed: int = 0  # reintentables (failed)
    dead: int = 0
    released: int = 0
    breaker_reason: str | None = None
    auto_resumed: str | None = None  # motivo de una pausa pasajera antigua del breaker que esta ejecución levantó
    by_kind: Counter[str] = field(default_factory=Counter)
    # Fase 3A: presupuestos estrictos y registro de lo consumido y lo pendiente
    requests_by_kind: Counter[str] = field(default_factory=Counter)  # peticiones HTTP REALES por tipo (incluye reintentos)
    max_requests: int | None = None
    max_reports: int | None = None  # tope de actas por ejecución (None = sin tope específico, como hasta ahora)
    deferred_kinds: list[str] = field(default_factory=list)  # tipos aplazados al agotar su presupuesto (siguen `pending`)
    pending_by_kind: dict[str, int] = field(default_factory=dict)  # trabajos vivos al terminar

    def as_dict(self) -> dict[str, Any]:
        return {"worker_id": self.worker_id, "stop_reason": self.stop_reason, "run_id": self.run_id, "requests": self.requests,
                "recovered": self.recovered, "done": self.done, "failed": self.failed, "dead": self.dead,
                "released": self.released, "breaker_reason": self.breaker_reason, "auto_resumed": self.auto_resumed,
                "by_kind": dict(self.by_kind), "requests_by_kind": dict(self.requests_by_kind),
                "budget": {"max_requests": self.max_requests, "max_reports": self.max_reports,
                           "reports_used": self.requests_by_kind.get(MATCH_REPORT, 0)},
                "deferred_kinds": self.deferred_kinds, "pending_by_kind": self.pending_by_kind}


def _engine(session_factory: Callable[[], Session]) -> Engine:
    with session_factory() as s:
        bind = s.get_bind()
        return bind.engine if isinstance(bind, Connection) else bind


def _finish_run(session_factory: Callable[[], Session], run_id: int, report: WorkerReport, status: str, now: datetime) -> None:
    with session_factory() as s, s.begin():
        run = s.get_one(IngestionRun, run_id)
        run.status, run.finished_at, run.error_summary = status, now, report.breaker_reason
        run.stats = report.as_dict()


def run_worker(session_factory: Callable[[], Session], fetcher: Fetcher, *, max_requests: int | None = None,
               until_empty: bool = True, worker_id: str = "worker-1", now: Callable[[], datetime] = lambda: datetime.now(UTC),
               config: QueueConfig | None = None, schedule: ScheduleConfig | None = None, work_dir: Path = DEFAULT_WORK_DIR,
               stop: StopFlag | None = None, handlers: dict[str, Handler] | None = None,
               sleep: Callable[[float], None] = time.sleep, poll_s: float = 30.0, min_priority: int | None = None,
               run_kind: str = "ingest_worker", reports_dir: Path | None = None, max_reports: int | None = None,
               kinds: Sequence[str] | None = None, exclude_kinds: Sequence[str] = (),
               competitions: Sequence[str] | None = None) -> WorkerReport:
    """Ejecuta la cola. Presupuestos ESTRICTOS (Fase 3A): `max_requests` peticiones HTTP reales en total (el Fetcher se niega a
    la siguiente) y `max_reports` actas: agotado el de actas, no se reclaman más (siguen `pending` con su prioridad, sin perderse
    ni duplicarse) aunque tengan más prioridad que el resto. Un solo trabajador por fuente a la vez (candado de PostgreSQL)."""
    cfg, sched, flag, table = config or load_config(), schedule or load_schedule(), stop or StopFlag(), handlers or HANDLERS
    source, engine = fetcher.source, _engine(session_factory)
    if max_requests is not None:
        fetcher.max_requests = max_requests
    report = WorkerReport(worker_id, max_requests=fetcher.max_requests, max_reports=max_reports)
    lock = engine.connect()  # candado de sesión: dura lo que dura este trabajador (también si muere: se libera con la conexión)
    if not lock.execute(text("SELECT pg_try_advisory_lock(:ns, hashtext(:src))"), {"ns": LOCK_NAMESPACE, "src": source}).scalar():
        lock.close()
        report.stop_reason = "locked"  # otro trabajador de la misma fuente está en marcha: no se toca nada
        return report
    try:
        return _run_locked(session_factory, fetcher, report, cfg, sched, flag, table, source, engine, worker_id, now, work_dir,
                           until_empty, sleep, poll_s, min_priority, reports_dir, run_kind, max_reports, kinds, tuple(exclude_kinds),
                           competitions)
    finally:
        lock.execute(text("SELECT pg_advisory_unlock(:ns, hashtext(:src))"), {"ns": LOCK_NAMESPACE, "src": source})
        lock.close()


def _run_locked(session_factory: Callable[[], Session], fetcher: Fetcher, report: WorkerReport, cfg: QueueConfig,
                sched: ScheduleConfig, flag: StopFlag, table: dict[str, Handler], source: str, engine: Engine, worker_id: str,
                now: Callable[[], datetime], work_dir: Path, until_empty: bool, sleep: Callable[[float], None], poll_s: float,
                min_priority: int | None, reports_dir: Path | None, run_kind: str, max_reports: int | None,
                kinds: Sequence[str] | None, exclude_kinds: tuple[str, ...],
                competitions: Sequence[str] | None = None) -> WorkerReport:
    with session_factory() as s, s.begin():
        control = controls.get_control(s, source)
        if control is not None and control.paused and control.opened_by == "breaker" and is_transient_reason(control.reason):
            # pausa antigua del breaker por un fallo pasajero de la web (antes pausaba): se levanta sola y se vuelve a intentar
            report.auto_resumed = control.reason
            controls.resume(s, source, now())
        report.recovered = len(jobs.recover_expired_leases(s, now()))
        run = IngestionRun(source=source, started_at=now(), status="running", input_path=f"queue:{source}", kind=run_kind)
        s.add(run)
        s.flush()
        report.run_id = run.id
    run_id = int(report.run_id or 0)
    try:
        _loop(session_factory, fetcher, engine, report, cfg, sched, flag, table, source, run_id, worker_id, now, work_dir,
              until_empty, sleep, poll_s, min_priority, reports_dir, max_reports, kinds, exclude_kinds, competitions)
    except BaseException:
        report.stop_reason = "aborted"  # muerte abrupta (señal dura, excepción inesperada): la ejecución queda `abandoned`
        with contextlib.suppress(Exception):  # los arrendamientos vencerán solos y otro trabajador recuperará el trabajo
            _finish_run(session_factory, run_id, report, "abandoned", now())
        raise
    report.requests = fetcher.requests
    with session_factory() as s:
        report.pending_by_kind = {k: v for k, st in jobs.progress(s, source)["by_kind"].items()
                                  if (v := st.get("pending", 0) + st.get("failed", 0))}
    status = {"breaker": "failed", "paused": "cancelled", "source_unavailable": "partial"}.get(
        report.stop_reason, "partial" if report.dead else "success")
    _finish_run(session_factory, run_id, report, status, now())
    return report


def _loop(session_factory: Callable[[], Session], fetcher: Fetcher, engine: Engine, report: WorkerReport, cfg: QueueConfig,
          sched: ScheduleConfig, flag: StopFlag, table: dict[str, Handler], source: str, run_id: int, worker_id: str,
          now: Callable[[], datetime], work_dir: Path, until_empty: bool, sleep: Callable[[float], None], poll_s: float,
          min_priority: int | None = None, reports_dir: Path | None = None, max_reports: int | None = None,
          kinds: Sequence[str] | None = None, exclude_kinds: tuple[str, ...] = (),
          competitions: Sequence[str] | None = None) -> None:
    while True:
        report.requests = fetcher.requests
        if flag.is_set():
            report.stop_reason = getattr(flag, "reason", None) or "signal"
            return
        if fetcher.requests >= fetcher.max_requests:
            report.stop_reason = "max_requests"
            return
        view: JobView | None = None
        with session_factory() as s, s.begin():
            if controls.is_paused(s, source):
                control = controls.get_control(s, source)
                report.stop_reason, report.breaker_reason = "paused", control.reason if control else None
                return
            blocked = list(exclude_kinds)
            if max_reports is not None and report.requests_by_kind[MATCH_REPORT] >= max_reports:
                blocked.append(MATCH_REPORT)  # presupuesto de actas agotado: aplazadas (siguen pending), nunca por prioridad
                if MATCH_REPORT not in report.deferred_kinds:
                    report.deferred_kinds.append(MATCH_REPORT)
            job = jobs.claim(s, worker_id, now(), cfg.lease_s, source=source, min_priority=min_priority, kinds=kinds,
                             exclude_kinds=blocked, competitions=competitions)
            if job is not None:
                job.run_id = run_id
                view = JobView(job.id, job.kind, dict(job.target), job.attempts, run_id)
        if view is None:
            if until_empty:
                report.stop_reason = "empty"
                return
            sleep(poll_s)
            continue
        report.by_kind[view.kind] += 1
        before = fetcher.requests
        stop = _run_job(session_factory, fetcher, engine, report, cfg, sched, table, source, view, now, work_dir, reports_dir)
        report.requests_by_kind[view.kind] += fetcher.requests - before
        if stop:
            return


def _fail(session_factory: Callable[[], Session], cfg: QueueConfig, report: WorkerReport, view: JobView, message: str,
          status: int | None, transient: bool, now: datetime) -> None:
    with session_factory() as s, s.begin():
        job = jobs.fail(s, view.id, message, status, transient=transient, now=now, base_s=cfg.retry_base_s,
                        factor=cfg.retry_factor, cap_s=cfg.retry_cap_s)
        if job.status == "dead":
            report.dead += 1
        else:
            report.failed += 1


def _release(session_factory: Callable[[], Session], report: WorkerReport, view: JobView, now: datetime) -> None:
    with session_factory() as s, s.begin():
        jobs.release(s, view.id, now)
    report.released += 1


def _run_job(session_factory: Callable[[], Session], fetcher: Fetcher, engine: Engine, report: WorkerReport, cfg: QueueConfig,
             sched: ScheduleConfig, table: dict[str, Handler], source: str, view: JobView, now: Callable[[], datetime],
             work_dir: Path, reports_dir: Path | None = None) -> bool:
    """Ejecuta un trabajo ya reclamado. Devuelve True si el trabajador debe detenerse."""
    handler = table.get(view.kind)
    if handler is None:
        _fail(session_factory, cfg, report, view, f"tipo de trabajo desconocido: {view.kind}", None, False, now())
        return False
    with session_factory() as s:  # alcance declarativo: un trabajo fuera de alcance falla sin red y sin reintentos
        blocked = gate_target(s, load_scope(), view.target, source, kind=view.kind)
    if blocked is not None:
        _fail(session_factory, cfg, report, view, f"fuera del alcance de ingesta: {blocked}", None, False, now())
        return False
    ctx = Context(session_factory, fetcher, engine, now, cfg, sched, work_dir, view, source, reports_dir)
    try:
        checkpoint = handler(ctx)
    except BreakerTripped as exc:
        # web caída (vacíos, 5xx, red, 403/429): se corta esta pasada sin pausar; la siguiente ejecución reintenta
        report.stop_reason = "source_unavailable" if is_transient_reason(exc.reason) else "breaker"
        report.breaker_reason = exc.reason
        _release(session_factory, report, view, now())  # idempotente: el interruptor ya lo devolvió a pending
        return True
    except (SourcePaused, RequestBudgetExceeded, NetworkDisabled) as exc:
        report.stop_reason = {SourcePaused: "paused", RequestBudgetExceeded: "max_requests"}.get(type(exc), "network_disabled")
        _release(session_factory, report, view, now())
        return True
    except JobError as exc:
        _fail(session_factory, cfg, report, view, str(exc), exc.http_status, exc.transient, now())
        return False
    except Exception as exc:  # noqa: BLE001 - un fallo inesperado de un trabajo no debe tumbar el trabajador
        fetcher.session.rollback()
        _fail(session_factory, cfg, report, view, f"{type(exc).__name__}: {exc}", None, True, now())
        return False
    with session_factory() as s, s.begin():
        jobs.save_checkpoint(s, view.id, checkpoint, now())
        jobs.complete(s, view.id, now())
    report.done += 1
    return False


# ---------------------------------------------------------------------------------------------------------------- CLI


def network_authorized(allow_flag: bool) -> bool:
    """Red real solo con DOS llaves a la vez: `--allow-network` Y `FUTSAL_RFFM_NETWORK_AUTHORIZED=1` (una sola no basta)."""
    return allow_flag and os.environ.get(NETWORK_ENV) == "1"


def add_work_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source", default="rffm")
    p.add_argument("--max-requests", type=int, default=None, help="tope de peticiones de esta ejecución (por defecto, el de la config)")
    p.add_argument("--until-empty", action="store_true", help="se detiene cuando no queda nada vencido (si no, espera trabajo)")
    p.add_argument("--allow-network", action="store_true", help=f"autoriza la red real (exige además {NETWORK_ENV}=1)")
    p.add_argument("--worker-id", default=f"worker-{os.getpid()}")
    p.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR, help="esqueletos, jornadas y tablas intermedias")
    p.add_argument("--max-reports", type=int, default=None,
                   help="tope ESTRICTO de actas en esta ejecución (las demás quedan pendientes, sin perderse ni duplicarse)")
    p.add_argument("--kinds", default=None, help="solo estos tipos de trabajo, separados por comas (p. ej. calendar,round_results)")
    p.add_argument("--competition", action="append", default=[], metavar="CODIGO",
                   help="lote: solo trabajos de esta competición (código oficial; repetible); el resto sigue pendiente")
    p.add_argument("--exclude-kinds", default="", help="nunca estos tipos de trabajo (p. ej. match_report para una carga sin actas)")


def _split(value: str | None) -> list[str]:
    return [k.strip() for k in (value or "").split(",") if k.strip()]


def run_cli(args: argparse.Namespace, engine: Engine | None = None) -> int:
    """`ingest work`: se niega a usar la red sin autorización explícita."""
    if not network_authorized(args.allow_network):
        print(f"Error: la descarga usaría la red de la RFFM; rechazado (requiere --allow-network Y {NETWORK_ENV}=1, con autorización del responsable).")
        return 2
    from futsal.db.session import make_engine

    engine = engine or make_engine()
    try:
        ensure_schema(engine)
    except MigrationMissing as exc:
        print(f"Error: {exc}")
        return 2
    cfg = load_config()
    session_factory = sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    fetcher = Fetcher(session, args.source, RateLimiter(cfg.min_interval_s, cfg.max_backoff_s), CircuitBreaker(args.source, cfg.breaker),
                      max_requests=args.max_requests or cfg.max_requests_per_run, network_authorized=True)
    flag = StopFlag()
    restore = install_signal_handlers(flag)
    try:
        report = run_worker(session_factory, fetcher, max_requests=args.max_requests or cfg.max_requests_per_run,
                            until_empty=args.until_empty, worker_id=args.worker_id, config=cfg, work_dir=args.work_dir, stop=flag,
                            max_reports=getattr(args, "max_reports", None), kinds=_split(getattr(args, "kinds", None)) or None,
                            exclude_kinds=_split(getattr(args, "exclude_kinds", "")),
                            competitions=list(getattr(args, "competition", []) or []) or None)
    finally:
        restore()
        fetcher.close()
        session.close()
    print(report.as_dict())
    if report.stop_reason == "locked":
        print("Otro trabajador de la misma fuente está en marcha: esta ejecución no ha hecho nada.")
    return 0 if report.stop_reason in ("empty", "max_requests", "signal", "source_unavailable", "locked") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m futsal.ingestion.queue.worker", description=__doc__)
    add_work_arguments(parser)
    return run_cli(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
