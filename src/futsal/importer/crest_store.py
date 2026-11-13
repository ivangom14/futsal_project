"""Copias locales normalizadas de los escudos (docs/design-system/04-escudos-equipos.md §4.4, paso 4).

Parte de las rutas que `crest-backfill` dejó en `teams.crest_url`, descarga cada imagen UNA vez de `appweb.rffm.es`
(secuencial, con pausa, tamaño máximo), la valida por su contenido (no por el `Content-Type`, que la RFFM sirve mal),
la normaliza a WebP 96×96 con lienzo transparente (`contain`) y la guarda como `<sha256[:20]>.webp`. El índice
`index.json` relaciona ruta RFFM → archivo; la API lo lee y el servicio de chat sirve los archivos.

Medido con los 14 escudos del grupo objetivo: 611,7 KB originales → 48,5 KB en WebP 96 px.
Uso previsto: pruebas privadas; para uso público hace falta autorización de la RFFM (§4.3).
"""

import hashlib
import io
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from futsal.config.targets import RFFM_ASSET_HOST
from futsal.db.models import Team
from futsal.importer.crest_index import INDEX, load_index
from futsal.ingestion.rffm.client import USER_AGENT

SIZE = 96
MAX_BYTES = 2_000_000
MAX_PIXELS = 25_000_000  # defensa ante imágenes «bomba»
ALLOWED_HOSTS = {"appweb.rffm.es"}
FORMATS = {"JPEG", "PNG", "GIF", "WEBP"}


def normalize(raw: bytes) -> bytes:
    """WebP SIZE×SIZE, transparente, imagen centrada sin deformar. ValueError si no es una imagen admitida."""
    if len(raw) > MAX_BYTES:
        raise ValueError("demasiado grande")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt, (w, h) = probe.format, probe.size
            probe.verify()
    except Exception as exc:  # cualquier fallo de Pillow = no es una imagen válida
        raise ValueError(f"no es una imagen: {type(exc).__name__}") from exc
    if fmt not in FORMATS or w * h > MAX_PIXELS:
        raise ValueError(f"formato o tamaño no admitido: {fmt} {w}×{h}")
    with Image.open(io.BytesIO(raw)) as img:
        rgba = img.convert("RGBA")
        rgba.thumbnail((SIZE, SIZE), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        canvas.paste(rgba, ((SIZE - rgba.width) // 2, (SIZE - rgba.height) // 2), rgba)
        out = io.BytesIO()
        canvas.save(out, "WEBP", quality=82, method=6)
        return out.getvalue()


def file_name(webp: bytes) -> str:
    return f"{hashlib.sha256(webp).hexdigest()[:20]}.webp"


def _save_index(folder: Path, index: dict[str, dict[str, Any]]) -> None:
    tmp = folder / f"{INDEX}.tmp"
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(folder / INDEX)


def http_fetch(url: str, timeout: float = 20.0) -> bytes:
    """GET de una imagen de la RFFM con límite de tamaño (se corta al superarlo)."""
    if urlsplit(url).hostname not in ALLOWED_HOSTS:
        raise ValueError(f"host no permitido: {url}")
    with httpx.stream("GET", url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=False) as resp:
        resp.raise_for_status()
        chunks, total = [], 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError("demasiado grande")
            chunks.append(chunk)
    return b"".join(chunks)


def download_crests(paths: list[str], folder: Path, *, fetch: Callable[[str], bytes] = http_fetch, delay: float = 1.0,
                    refresh: bool = False, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Descarga y normaliza las rutas que falten (o todas con `refresh`). Una petición cada `delay` segundos como mínimo."""
    folder.mkdir(parents=True, exist_ok=True)
    index = load_index(folder)
    todo = [p for p in dict.fromkeys(paths) if refresh or p not in index or not (folder / index[p]["file"]).exists()]
    stats: dict[str, Any] = {"paths": len(set(paths)), "already": len(set(paths)) - len(todo), "downloaded": 0, "failed": 0,
                             "bytes_source": 0, "bytes_stored": 0, "errors": []}
    for i, path in enumerate(todo):
        if i:
            sleep(delay)
        try:
            raw = fetch(f"{RFFM_ASSET_HOST}{path}")
            webp = normalize(raw)
        except (httpx.HTTPError, ValueError) as exc:
            stats["failed"] += 1
            stats["errors"].append(f"{path}: {type(exc).__name__}: {str(exc)[:80]}")
            continue
        name = file_name(webp)
        target = folder / name
        if not target.exists():
            target.write_bytes(webp)
        index[path] = {"file": name, "bytes_source": len(raw), "bytes": len(webp), "fetched_at": datetime.now(UTC).isoformat(timespec="seconds")}
        stats["downloaded"] += 1
        stats["bytes_source"] += len(raw)
        stats["bytes_stored"] += len(webp)
        _save_index(folder, index)  # tras cada imagen: una interrupción no pierde lo ya descargado
    _save_index(folder, index)
    return stats


def run_crest_download(engine: Engine, folder: Path, *, delay: float = 1.0, refresh: bool = False, limit: int | None = None) -> dict[str, Any]:
    """Rutas distintas de `teams.crest_url` (rellenadas por `crest-backfill`) → copias locales."""
    with Session(engine) as session:
        paths = [p for p in session.scalars(select(Team.crest_url).where(Team.crest_url.is_not(None)).distinct()) if p and p.startswith("/")]
    paths = sorted(paths)[:limit] if limit else sorted(paths)
    return download_crests(paths, folder, delay=delay, refresh=refresh)
