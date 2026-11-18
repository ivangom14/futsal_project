"""Interruptor automático (circuit breaker) de un `source`. Los umbrales vienen de `config/ingest_schedule.json`.

Al abrirse devuelve a `pending`, sin consumir intento, el trabajo en curso y detiene la pasada. Dos tipos:
- PASAJERO (respuestas vacías, 5xx o de red, HTTP 403/429): la web de la RFFM se cae a menudo, sobre todo en fin de semana. Solo se
  corta esta pasada; NO se pausa el source y la siguiente ejecución (cron/timer) lo vuelve a intentar.
- DE DATOS (`scope_mismatch`: otra temporada/competición/grupo con HTTP 200; demasiados `schema_error`: cambió el formato): seguir
  podría guardar datos equivocados, así que pausa el source (`controls.pause(..., by='breaker')`) hasta que alguien lo revise.
El estado de rachas y ventana vive en memoria y dura lo que la ejecución (un proceso, un `source`).
"""

import re
from collections import deque
from datetime import datetime

from sqlalchemy.orm import Session

from futsal.ingestion.queue import controls, jobs
from futsal.ingestion.queue.controls import BreakerConfig

TRANSIENT_MARKERS = ("cuerpos null o vacíos", "errores 5xx o de red")
TRANSIENT_STATUS = re.compile(r"^HTTP \d{3} en ")  # 403/429 de `open_on_status` (el «HTTP 200» de un ámbito distinto NO lo es)


def is_transient_reason(reason: str | None) -> bool:
    """¿El motivo de una pausa del breaker es un fallo pasajero de la web? (los de datos: «ámbito distinto…», «… páginas con `schema_error`»)."""
    return bool(reason) and (any(m in str(reason) for m in TRANSIENT_MARKERS) or bool(TRANSIENT_STATUS.match(str(reason))))


class CircuitBreaker:
    def __init__(self, source: str, config: BreakerConfig | None = None) -> None:
        self.source = source
        self.config = config or BreakerConfig()
        self._server_errors = 0
        self._null_bodies = 0
        self._last_null_url: str | None = None
        self._window: deque[bool] = deque(maxlen=self.config.window)  # True = forma incorrecta
        self.opened_reason: str | None = None

    def on_response(self, session: Session, *, url: str, status_code: int | None, parse_status: str,
                    job_id: int | None = None, now: datetime | None = None) -> str | None:
        """Informa de una respuesta a nivel de transporte (`ok`, `null_body`, `http_error`, `network_error`)."""
        cfg = self.config
        if status_code in cfg.open_on_status:
            return self._open(session, f"HTTP {status_code} en {url}", job_id, now, persistent=False)
        if parse_status == "network_error" or (status_code is not None and status_code >= 500):
            self._server_errors += 1
            self._null_bodies, self._last_null_url = 0, None
            if self._server_errors >= cfg.consecutive_server_errors:
                return self._open(session, f"{self._server_errors} errores 5xx o de red consecutivos (último: {url})",
                                  job_id, now, persistent=False)
            return None
        self._server_errors = 0
        if parse_status == "null_body":
            if url != self._last_null_url:  # solo cuentan URLs distintas
                self._null_bodies += 1
                self._last_null_url = url
            if self._null_bodies >= cfg.consecutive_null_bodies:
                return self._open(session, f"{self._null_bodies} cuerpos null o vacíos consecutivos en URLs distintas "
                                           f"(último: {url})", job_id, now, persistent=False)
        else:
            self._null_bodies, self._last_null_url = 0, None
        return None

    def on_parse(self, session: Session, *, url: str, parse_status: str, job_id: int | None = None,
                 now: datetime | None = None) -> str | None:
        """Informa del resultado de validar una página (`ok`, `scope_mismatch`, `schema_error`).

        Una sola página `scope_mismatch` (la RFFM devolvió otra temporada, competición, grupo o modalidad con HTTP 200)
        abre el interruptor de inmediato (ingestion-proposal-p1 §3); el umbral `bad_shape_ratio` rige solo `schema_error`.
        """
        if parse_status == "scope_mismatch":
            return self._open(session, f"ámbito distinto del pedido (HTTP 200 con otra temporada, competición, grupo o "
                                       f"modalidad) en {url}", job_id, now)
        self._window.append(parse_status == "schema_error")
        cfg = self.config
        total, bad = len(self._window), sum(self._window)
        if total >= cfg.min_window and bad / total > cfg.bad_shape_ratio:
            return self._open(session, f"{bad}/{total} páginas con `schema_error` (último en {url})",
                              job_id, now)
        return None

    def _open(self, session: Session, reason: str, job_id: int | None, now: datetime | None, persistent: bool = True) -> str:
        self.opened_reason = reason
        if persistent:  # solo los problemas de DATOS pausan el source; los pasajeros cortan esta pasada y nada más
            controls.pause(session, self.source, reason, by="breaker", now=now)
        if job_id is not None:
            jobs.release(session, job_id, now)
        return reason
