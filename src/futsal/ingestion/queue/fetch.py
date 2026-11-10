"""Descarga controlada para la cola: solo GET a hosts permitidos, con ritmo, registro en `raw_fetches` e interruptor.

La red real está DESACTIVADA: `Fetcher.get` exige `network_authorized=True`, y sin cliente inyectado se construye uno real
solo con esa autorización. No interpreta el contenido: el análisis informa luego con `report_parse`.
"""

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from futsal.db.models import RawFetch
from futsal.ingestion.queue import controls
from futsal.ingestion.queue.breaker import CircuitBreaker
from futsal.ingestion.queue.ratelimit import RateLimiter
from futsal.ingestion.rffm.client import ALLOWED_HOSTS, USER_AGENT

DEFAULT_SNAPSHOT_DIR = Path("data/raw/rffm/queue")
PARSE_STATUSES = ("ok", "null_body", "scope_mismatch", "schema_error", "http_error", "network_error")


class NetworkDisabled(RuntimeError):
    """Se intentó usar la red sin `network_authorized=True`."""


class RequestBudgetExceeded(RuntimeError):
    """Se alcanzó `max_requests` en esta ejecución."""


class SourcePaused(RuntimeError):
    """El source está pausado (interruptor manual o del breaker)."""


@dataclass
class FetchResult:
    url: str
    status_code: int | None
    body: bytes
    parse_status: str
    raw_fetch_id: int
    snapshot_path: str | None
    tripped: str | None = None  # motivo si esta petición abrió el interruptor

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def _extension(content_type: str | None) -> str:
    ct = (content_type or "").lower()
    return "json" if "json" in ct else "html" if "html" in ct else "bin"


class Fetcher:
    def __init__(self, session: Session, source: str, limiter: RateLimiter, breaker: CircuitBreaker, *,
                 client: httpx.Client | None = None, max_requests: int = 500, snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
                 network_authorized: bool = False, timeout: float = 30.0,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.session, self.source, self.limiter, self.breaker = session, source, limiter, breaker
        self.max_requests, self.snapshot_dir, self.network_authorized = max_requests, snapshot_dir, network_authorized
        self._now = now
        self._owns_client = client is None
        self._client = client
        self._timeout = timeout
        self.requests = 0

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=self._timeout, follow_redirects=False)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def get(self, url: str, job_id: int | None = None) -> FetchResult:
        host = urlparse(url).hostname
        if host not in ALLOWED_HOSTS:
            raise ValueError(f"Host no permitido: {url}")
        if not self.network_authorized:
            raise NetworkDisabled("red desactivada: hace falta network_authorized=True")
        if controls.is_paused(self.session, self.source):
            raise SourcePaused(f"{self.source} está pausado")
        if self.requests >= self.max_requests:
            raise RequestBudgetExceeded(f"tope de {self.max_requests} peticiones por ejecución")
        self.limiter.wait(host)
        self.requests += 1
        requested_at = self._now()
        started = time.monotonic()
        status: int | None = None
        body = b""
        content_type: str | None = None
        error: str | None = None
        try:
            resp = self._http().get(url)
            status, body, content_type = resp.status_code, resp.content, resp.headers.get("content-type")
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
        elapsed_ms = int((time.monotonic() - started) * 1000)

        if error is not None:
            parse_status = "network_error"
        elif status is not None and status >= 400:
            parse_status, error = "http_error", f"HTTP {status}"
        elif not body.strip() or body.strip() == b"null":
            parse_status = "null_body"
        else:
            parse_status = "ok"

        sha = path = None
        if body:
            sha = hashlib.sha256(body).hexdigest()
            file = self.snapshot_dir / requested_at.strftime("%Y-%m-%d") / f"{sha}.{_extension(content_type)}"
            file.parent.mkdir(parents=True, exist_ok=True)
            if not file.exists():
                file.write_bytes(body)
            path = str(file)
        row = RawFetch(job_id=job_id, source=self.source, url=url, method="GET", requested_at=requested_at,
                       status_code=status, bytes=len(body) if body else None, sha256=sha, content_type=content_type,
                       snapshot_path=path, elapsed_ms=elapsed_ms, parse_status=parse_status, error=error)
        self.session.add(row)
        self.session.flush()

        if parse_status == "ok":
            self.limiter.reward()
        else:
            self.limiter.penalize()
        tripped = self.breaker.on_response(self.session, url=url, status_code=status, parse_status=parse_status,
                                           job_id=job_id, now=requested_at)
        return FetchResult(url, status, body, parse_status, row.id, path, tripped)

    def report_parse(self, result: FetchResult, parse_status: str, error: str | None = None,
                     job_id: int | None = None) -> str | None:
        """El análisis informa del resultado de validar la página; lo anota en `raw_fetches` y alimenta al breaker."""
        if parse_status not in ("ok", "scope_mismatch", "schema_error"):
            raise ValueError(f"parse_status no válido aquí: {parse_status}")
        row = self.session.get(RawFetch, result.raw_fetch_id)
        if row is not None:
            row.parse_status, row.error = parse_status, error and error[:500]
            self.session.flush()
        result.parse_status = parse_status
        tripped = self.breaker.on_parse(self.session, url=result.url, parse_status=parse_status, job_id=job_id)
        if tripped:
            result.tripped = tripped
        return tripped
