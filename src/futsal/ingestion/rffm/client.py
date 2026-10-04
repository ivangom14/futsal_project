"""Cliente HTTP limitado a RFFM. Solo descarga; no interpreta."""

from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

ALLOWED_HOSTS = {"www.rffm.es", "rffm.es"}
BASE_URL = "https://www.rffm.es/competicion/resultados-y-jornadas"
USER_AGENT = "futsal-project/0.0.1 (personal research; low volume)"


def round_url(season: str, competition: str, group: str, round_number: int, game_type: str) -> str:
    query = {
        "temporada": season,
        "competicion": competition,
        "grupo": group,
        "jornada": round_number,
        "tipojuego": game_type,
    }
    return f"{BASE_URL}?{urlencode(query)}"


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
