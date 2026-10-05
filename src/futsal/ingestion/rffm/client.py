"""Cliente HTTP limitado a RFFM. Solo descarga; no interpreta."""

import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

ALLOWED_HOSTS = {"www.rffm.es", "rffm.es"}
BASE_URL = "https://www.rffm.es/competicion/resultados-y-jornadas"
USER_AGENT = "futsal-project/0.0.1 (personal research; low volume)"


def round_url(season: str, competition: str, group: str, round_number: int | str, game_type: str) -> str:
    query = {
        "temporada": season,
        "competicion": competition,
        "grupo": group,
        "jornada": round_number,
        "tipojuego": game_type,
    }
    return f"{BASE_URL}?{urlencode(query)}"


def match_report_url(codacta: str, season: str, competition: str, group: str) -> str:
    """URL pública del acta (patrón del JS de la web: `/acta-partido/<codacta>?temporada&competicion&grupo`)."""
    query = urlencode({"temporada": season, "competicion": competition, "grupo": group})
    return f"https://www.rffm.es/acta-partido/{codacta}?{query}"


def fetch_html(url: str, timeout: float = 30.0) -> str:
    if urlparse(url).hostname not in ALLOWED_HOSTS:
        raise ValueError(f"Host no permitido: {url}")
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def read_or_fetch(url: str, snapshot: Path, refresh: bool = False) -> str:
    """Reutiliza el snapshot local si existe; solo descarga con `refresh` o si falta."""
    if snapshot.exists() and not refresh:
        return snapshot.read_text(encoding="utf-8")
    html = fetch_html(url)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(html, encoding="utf-8")
    return html


class FetchError(RuntimeError):
    """Fallo de descarga tras agotar los reintentos permitidos."""


class PoliteFetcher:
    """Descarga secuencial con pausa entre peticiones y reintentos limitados (solo temporales)."""

    def __init__(
        self,
        delay: float = 2.0,
        timeout: float = 30.0,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        max_retry_after: float = 60.0,
    ) -> None:
        self.delay, self.timeout, self.max_retries = delay, timeout, max_retries
        self.max_retry_after, self._sleep = max_retry_after, sleep
        self.requests = 0

    def __call__(self, url: str) -> str:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if self.requests:
                self._sleep(self.delay)
            self.requests += 1
            wait = self.delay * (attempt + 2)
            try:
                return fetch_html(url, self.timeout)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status != 429 and status < 500:
                    raise FetchError(f"HTTP {status}") from exc
                retry_after = exc.response.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    wait = min(float(retry_after), self.max_retry_after)
                last = exc
            except httpx.TransportError as exc:
                last = exc
            if attempt < self.max_retries:
                self._sleep(wait)
        raise FetchError(f"sin respuesta válida tras {self.max_retries + 1} intentos: {last!r}")
