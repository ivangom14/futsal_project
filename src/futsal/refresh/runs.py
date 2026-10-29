"""Ciclo de vida de las ejecuciones en `ingestion_runs`: inicio, cierre y recuperación de las abandonadas."""

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from futsal.db.models import IngestionRun

DEFAULT_ABANDON_HOURS = 6.0
ENV_ABANDON_HOURS = "FUTSAL_RUN_ABANDON_HOURS"
FINAL_STATUSES = ("success", "no_changes", "partial", "failed", "cancelled", "abandoned")


def abandon_hours(explicit: float | None = None) -> float:
    """Antigüedad (horas) a partir de la cual una ejecución `running` se considera abandonada: argumento > entorno > 6 h."""
    raw: float | str | None = explicit if explicit is not None else os.environ.get(ENV_ABANDON_HOURS)
    if raw is None or raw == "":
        return DEFAULT_ABANDON_HOURS
    try:
        hours = float(raw)
    except ValueError as exc:
        raise ValueError(f"{ENV_ABANDON_HOURS} debe ser un número de horas, no {raw!r}") from exc
    if hours <= 0:
        raise ValueError("la antigüedad de abandono debe ser mayor que 0 horas")
    return hours


def start_run(engine: Engine, kind: str, scope: dict[str, Any], now: datetime | None = None) -> int:
    with Session(engine) as session, session.begin():
        run = IngestionRun(source="rffm", started_at=now or datetime.now(UTC), status="running", input_path=f"refresh:{kind}",
                           kind=kind, stats={"scope": scope})
        session.add(run)
        session.flush()
        return int(run.id)


def finish_run(engine: Engine, run_id: int, status: str, stats: dict[str, Any], *, error: str | None = None,
               counts: dict[str, int] | None = None, now: datetime | None = None) -> bool:
    """Cierra la ejecución solo si sigue `running` (una ya recuperada como abandonada no se pisa). False si no se pudo cerrar."""
    assert status in FINAL_STATUSES
    values: dict[str, Any] = {"status": status, "finished_at": now or datetime.now(UTC), "stats": stats,
                              "error_summary": (error or None) and error[:500]}
    for column in ("rounds_processed", "matches_processed", "records_inserted", "records_updated", "records_unchanged",
                   "observations_created", "issues_created"):
        if counts and column in counts:
            values[column] = counts[column]
    with Session(engine) as session, session.begin():
        done = session.execute(update(IngestionRun).where(IngestionRun.id == run_id, IngestionRun.status == "running")
                               .values(**values))
        return bool(done.rowcount)  # type: ignore[attr-defined]


def recover_abandoned(engine: Engine, *, max_age_hours: float, now: datetime | None = None,
                      dry_run: bool = False) -> list[dict[str, Any]]:
    """Marca como `abandoned` las ejecuciones `running` iniciadas hace más de `max_age_hours`. Idempotente: solo toca `running`.

    No se borra ni se reintenta nada: el estado de las tablas lo decidieron las transacciones ya confirmadas. Una ejecución
    realmente activa de más de ese tiempo también se marcaría (por eso el umbral es configurable y por defecto holgado).
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=max_age_hours)
    recovered: list[dict[str, Any]] = []
    with Session(engine) as session, session.begin():
        stmt = (select(IngestionRun).where(IngestionRun.status == "running", IngestionRun.started_at < cutoff)
                .order_by(IngestionRun.id).with_for_update(skip_locked=True))
        for run in session.scalars(stmt):
            recovered.append({"run_id": run.id, "kind": run.kind, "started_at": run.started_at.isoformat(),
                              "age_hours": round((now - run.started_at).total_seconds() / 3600, 1)})
            if dry_run:
                continue
            run.status, run.finished_at = "abandoned", now
            run.error_summary = f"abandonada: sin cierre tras {max_age_hours:g} h (recuperación controlada)"
            run.stats = {**(run.stats or {}), "recovered": {"at": now.isoformat(), "max_age_hours": max_age_hours}}
    return recovered


def recent_runs(engine: Engine, limit: int = 10, kind: str | None = None) -> list[dict[str, Any]]:
    with Session(engine) as session:
        stmt = select(IngestionRun).order_by(IngestionRun.id.desc()).limit(limit)
        if kind:
            stmt = stmt.where(IngestionRun.kind == kind)
        return [{"run_id": r.id, "kind": r.kind, "status": r.status, "started_at": r.started_at.isoformat(),
                 "finished_at": r.finished_at.isoformat() if r.finished_at else None, "error": r.error_summary,
                 "inserted": r.records_inserted, "updated": r.records_updated, "unchanged": r.records_unchanged,
                 "issues": r.issues_created, "stats": r.stats} for r in session.scalars(stmt)]
