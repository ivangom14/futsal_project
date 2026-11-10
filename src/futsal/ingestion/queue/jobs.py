"""Cola de trabajos `ingest_jobs`. Sin estado global: todo pasa por la `Session` recibida.

Las funciones solo hacen `flush`; confirma (`commit`) quien llama. Los intentos se consumen en `fail`, nunca en `claim`,
de modo que recuperar un arrendamiento o devolver un trabajo a `pending` no gasta intentos.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from futsal.db.models import IngestControl, IngestJob

LIVE = ("pending", "running", "failed")
RETRY_BASE_S, RETRY_FACTOR, RETRY_CAP_S = 10.0, 3.0, 900.0


def canonical_key(target: dict[str, Any]) -> str:
    return json.dumps(target, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _live_job(session: Session, source: str, kind: str, key: str) -> IngestJob | None:
    return session.execute(select(IngestJob).where(
        IngestJob.source == source, IngestJob.kind == kind, IngestJob.target_key == key, IngestJob.status.in_(LIVE))
        .execution_options(populate_existing=True)).scalar_one_or_none()


def enqueue(session: Session, source: str, kind: str, target: dict[str, Any], *, priority: int = 0,
            not_before: datetime | None = None, max_attempts: int = 4, run_id: int | None = None,
            target_key: str | None = None, now: datetime | None = None) -> IngestJob:
    """Idempotente: con un trabajo vivo de la misma clave devuelve ese (subiendo su prioridad o adelantando `not_before`)."""
    now = now or datetime.now(UTC)
    key = target_key or canonical_key(target)
    when = not_before or now
    stmt = insert(IngestJob).values(
        source=source, kind=kind, target=target, target_key=key, priority=priority, not_before=when, status="pending",
        attempts=0, max_attempts=max_attempts, run_id=run_id, created_at=now, updated_at=now,
    ).on_conflict_do_nothing(index_elements=["source", "kind", "target_key"],
                             index_where=text("status IN ('pending','running','failed')")).returning(IngestJob.id)
    new_id = session.execute(stmt).scalar_one_or_none()
    if new_id is not None:
        return session.execute(select(IngestJob).where(IngestJob.id == new_id)
                               .execution_options(populate_existing=True)).scalar_one()
    job = _live_job(session, source, kind, key)
    if job is None:  # la fila viva terminó entre el conflicto y la lectura: se vuelve a intentar una vez
        return enqueue(session, source, kind, target, priority=priority, not_before=not_before,
                       max_attempts=max_attempts, run_id=run_id, target_key=key, now=now)
    changed = False
    if priority > job.priority:
        job.priority, changed = priority, True
    if job.status != "running" and when < job.not_before:
        job.not_before, changed = when, True
    if changed:
        job.updated_at = now
        session.flush()
    return job


def claim(session: Session, worker: str, now: datetime, lease_s: int, *, source: str | None = None) -> IngestJob | None:
    """Toma el trabajo más prioritario ya vencido (`pending`, o `failed` con la espera cumplida) de un source no pausado."""
    paused = exists().where(IngestControl.source == IngestJob.source, IngestControl.paused.is_(True))
    stmt = (select(IngestJob)
            .where(IngestJob.status.in_(("pending", "failed")), IngestJob.not_before <= now, ~paused)
            .order_by(IngestJob.priority.desc(), IngestJob.not_before, IngestJob.id)
            .limit(1).with_for_update(skip_locked=True, of=IngestJob))
    if source is not None:
        stmt = stmt.where(IngestJob.source == source)
    job = session.execute(stmt.execution_options(populate_existing=True)).scalar_one_or_none()
    if job is None:
        return None
    job.status, job.worker = "running", worker
    job.lease_until = now + timedelta(seconds=lease_s)
    job.updated_at = now
    session.flush()
    return job


def _get(session: Session, job_id: int) -> IngestJob:
    job = session.get(IngestJob, job_id, populate_existing=True)
    if job is None:
        raise LookupError(f"trabajo inexistente: {job_id}")
    return job


def complete(session: Session, job_id: int, now: datetime | None = None) -> IngestJob:
    now = now or datetime.now(UTC)
    job = _get(session, job_id)
    job.status, job.finished_at, job.updated_at = "done", now, now
    job.lease_until = job.worker = None
    session.flush()
    return job


def backoff_s(attempts: int, base_s: float = RETRY_BASE_S, factor: float = RETRY_FACTOR, cap_s: float = RETRY_CAP_S) -> float:
    """Espera tras el fallo número `attempts` (1-based): base, base·factor, … con tope."""
    return float(min(cap_s, base_s * factor ** max(attempts - 1, 0)))


def fail(session: Session, job_id: int, error: str, http_status: int | None = None, *, transient: bool,
         now: datetime | None = None, base_s: float = RETRY_BASE_S, factor: float = RETRY_FACTOR,
         cap_s: float = RETRY_CAP_S) -> IngestJob:
    """Transitorio: `failed` con `not_before` exponencial, o `dead` al agotar `max_attempts`. No transitorio: `dead`."""
    now = now or datetime.now(UTC)
    job = _get(session, job_id)
    job.attempts += 1
    job.last_error, job.last_http_status, job.updated_at = error[:2000], http_status, now
    job.lease_until = job.worker = None
    if transient and job.attempts < job.max_attempts:
        job.status = "failed"
        job.not_before = now + timedelta(seconds=backoff_s(job.attempts, base_s, factor, cap_s))
    else:
        job.status, job.finished_at = "dead", now
    session.flush()
    return job


def release(session: Session, job_id: int, now: datetime | None = None) -> IngestJob:
    """Devuelve un trabajo en curso a `pending` sin consumir intento (p. ej. al abrirse el interruptor)."""
    job = _get(session, job_id)
    if job.status == "running":
        job.status, job.updated_at = "pending", now or datetime.now(UTC)
        job.lease_until = job.worker = None
        session.flush()
    return job


def cancel(session: Session, job_id: int, now: datetime | None = None) -> bool:
    """Cancela un trabajo vivo. False si no existe o ya terminó."""
    now = now or datetime.now(UTC)
    job = session.get(IngestJob, job_id, populate_existing=True)
    if job is None or job.status not in LIVE:
        return False
    job.status, job.finished_at, job.updated_at = "cancelled", now, now
    job.lease_until = job.worker = None
    session.flush()
    return True


def recover_expired_leases(session: Session, now: datetime | None = None) -> list[int]:
    """`running` con arrendamiento vencido -> `pending`, sin consumir intento. Devuelve los ids recuperados."""
    now = now or datetime.now(UTC)
    rows = session.execute(update(IngestJob)
                           .where(IngestJob.status == "running", IngestJob.lease_until < now)
                           .values(status="pending", lease_until=None, worker=None, updated_at=now)
                           .returning(IngestJob.id)).all()
    session.flush()
    return sorted(r[0] for r in rows)


def save_checkpoint(session: Session, job_id: int, checkpoint: dict[str, Any], now: datetime | None = None) -> None:
    job = _get(session, job_id)
    job.checkpoint, job.updated_at = checkpoint, now or datetime.now(UTC)
    session.flush()


def progress(session: Session, source: str) -> dict[str, Any]:
    """Recuentos por estado y por tipo (`by_kind[kind][status]`)."""
    rows = session.execute(select(IngestJob.kind, IngestJob.status, func.count())
                           .where(IngestJob.source == source).group_by(IngestJob.kind, IngestJob.status)).all()
    by_status: dict[str, int] = {}
    by_kind: dict[str, dict[str, int]] = {}
    for kind, status, n in rows:
        by_status[status] = by_status.get(status, 0) + n
        by_kind.setdefault(kind, {})[status] = n
    return {"total": sum(by_status.values()), "by_status": by_status, "by_kind": by_kind}
