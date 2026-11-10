"""Interruptor por fuente (`ingest_controls`) y parámetros de la cola (`config/ingest_schedule.json`).

Las funciones usan la `Session` recibida y solo hacen `flush`: confirma (`commit`) quien llama.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from futsal.db.models import IngestControl

CONFIG_PATH = Path(__file__).parents[2] / "config" / "ingest_schedule.json"
OPENED_BY = ("manual", "breaker")


@dataclass(frozen=True)
class BreakerConfig:
    open_on_status: tuple[int, ...] = (403, 429)
    consecutive_server_errors: int = 3
    consecutive_null_bodies: int = 2
    bad_shape_ratio: float = 0.2
    min_window: int = 5
    window: int = 20


@dataclass(frozen=True)
class QueueConfig:
    min_interval_s: float = 3.0
    max_backoff_s: float = 60.0
    max_attempts: int = 4
    lease_s: int = 300
    max_requests_per_run: int = 500
    retry_base_s: float = 10.0
    retry_factor: float = 3.0
    retry_cap_s: float = 900.0
    breaker: BreakerConfig = field(default_factory=BreakerConfig)


def load_config(path: Path | None = None) -> QueueConfig:
    raw: dict[str, Any] = json.loads((path or CONFIG_PATH).read_text(encoding="utf-8"))
    retry = raw.get("retry", {})
    br = raw.get("breaker", {})
    d = BreakerConfig()
    breaker = BreakerConfig(
        open_on_status=tuple(br.get("open_on_status", d.open_on_status)),
        consecutive_server_errors=int(br.get("consecutive_server_errors", d.consecutive_server_errors)),
        consecutive_null_bodies=int(br.get("consecutive_null_bodies", d.consecutive_null_bodies)),
        bad_shape_ratio=float(br.get("bad_shape_ratio", d.bad_shape_ratio)),
        min_window=int(br.get("min_window", d.min_window)),
        window=int(br.get("window", d.window)))
    q = QueueConfig()
    return QueueConfig(
        min_interval_s=float(raw.get("min_interval_s", q.min_interval_s)),
        max_backoff_s=float(raw.get("max_backoff_s", q.max_backoff_s)),
        max_attempts=int(raw.get("max_attempts", q.max_attempts)),
        lease_s=int(raw.get("lease_s", q.lease_s)),
        max_requests_per_run=int(raw.get("max_requests_per_run", q.max_requests_per_run)),
        retry_base_s=float(retry.get("base_s", q.retry_base_s)),
        retry_factor=float(retry.get("factor", q.retry_factor)),
        retry_cap_s=float(retry.get("cap_s", q.retry_cap_s)),
        breaker=breaker)


def pause(session: Session, source: str, reason: str, by: str = "manual", now: datetime | None = None) -> None:
    """Pausa el `source` (idempotente: repetir actualiza motivo y autor)."""
    if by not in OPENED_BY:
        raise ValueError(f"opened_by debe ser uno de {OPENED_BY}, no {by!r}")
    now = now or datetime.now(UTC)
    stmt = insert(IngestControl).values(source=source, paused=True, reason=reason, opened_by=by, opened_at=now, updated_at=now)
    session.execute(stmt.on_conflict_do_update(
        index_elements=[IngestControl.source],
        set_={"paused": True, "reason": reason, "opened_by": by, "opened_at": now, "updated_at": now}))
    session.flush()


def resume(session: Session, source: str, now: datetime | None = None) -> bool:
    """Reanuda el `source`. Devuelve True si estaba pausado."""
    control = session.get(IngestControl, source, populate_existing=True)
    if control is None or not control.paused:
        return False
    control.paused, control.reason, control.opened_by = False, None, None
    control.updated_at = now or datetime.now(UTC)
    session.flush()
    return True


def get_control(session: Session, source: str) -> IngestControl | None:
    return session.execute(select(IngestControl).where(IngestControl.source == source)
                           .execution_options(populate_existing=True)).scalar_one_or_none()


def is_paused(session: Session, source: str) -> bool:
    control = get_control(session, source)
    return bool(control and control.paused)
