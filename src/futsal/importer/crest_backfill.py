"""Escudos de equipos desde las instantáneas RFFM ya descargadas, sin red (docs/design-system/04-escudos-equipos.md).

La RFFM publica la ruta del escudo junto al código del equipo en páginas que el proyecto ya guarda: jornadas
(`CodEquipo_local`/`url_img_local`), calendario (`codigo_equipo_local`/`escudo_equipo_local`), clasificación
(`codequipo`/`url_img`) y acta (`codigo_equipo_local`/`escudo_local`). Se recorre `__NEXT_DATA__` (HTML) o el JSON y se
guarda en `teams.crest_url` la RUTA relativa (`/pnfg/pimg/Clubes/...`), sin descargar la imagen: el host se añade al servirla.

- El escudo es un atributo del equipo en una fecha, nunca una clave (el número del escudo no identifica al club).
- Si un equipo aparece con escudos distintos, gana la instantánea más reciente (fecha de modificación del archivo).
- Solo se aceptan rutas de imagen bajo `/pnfg/pimg/` (nada de URLs arbitrarias). Dos ejecuciones seguidas: la segunda, 0 cambios.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from futsal.db.models import Team

PAIRS = (
    ("CodEquipo_local", "url_img_local"), ("CodEquipo_visitante", "url_img_visitante"),
    ("codigo_equipo_local", "escudo_equipo_local"), ("codigo_equipo_visitante", "escudo_equipo_visitante"),
    ("codigo_equipo_local", "escudo_local"), ("codigo_equipo_visitante", "escudo_visitante"),
    ("codequipo", "url_img"),
)
_PATH = re.compile(r"^/pnfg/pimg/[\w./%()\- ]+\.(?:png|jpe?g|gif|webp)$", re.IGNORECASE)
_NEXT = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)


def crest_path(value: Any) -> str | None:
    """Ruta relativa normalizada del escudo (sin `?nova=1`), o None si no es una imagen de la RFFM."""
    if not isinstance(value, str) or not value.strip():
        return None
    parts = urlsplit(value.strip())
    if parts.scheme and not (parts.scheme == "https" and (parts.hostname or "").endswith("rffm.es")):
        return None
    path = parts.path if parts.path.startswith("/") else f"/{parts.path}"
    return path if _PATH.match(path) else None


def extract_crests(data: Any) -> dict[str, str]:
    """{código de equipo: ruta del escudo} de cualquier estructura de la RFFM (recorrido completo)."""
    out: dict[str, str] = {}
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for code_key, img_key in PAIRS:
                code, path = node.get(code_key), crest_path(node.get(img_key))
                if code not in (None, "") and path:
                    out[str(code).strip()] = path
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


def _load(path: Path) -> Any:
    text = path.read_text(encoding="utf-8", errors="ignore")
    m = _NEXT.search(text)
    try:
        return json.loads(m.group(1) if m else text)
    except ValueError:
        return None


def scan(root: Path) -> tuple[dict[str, str], int]:
    """Escudos de todas las instantáneas (.html/.json) bajo `root`; la más reciente manda. Devuelve (mapa, archivos leídos)."""
    files: Iterator[Path] = (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in (".html", ".json"))
    crests: dict[str, str] = {}
    read = 0
    for path in sorted(files, key=lambda p: p.stat().st_mtime):
        data = _load(path)
        if data is None:
            continue
        read += 1
        crests.update(extract_crests(data))
    return crests, read


def run_crest_backfill(engine: Engine, root: Path, *, dry_run: bool = False) -> dict[str, int]:
    """Actualiza `teams.crest_url` (fuente `rffm`) por código de equipo. Con `dry_run` calcula los cambios y los deshace."""
    crests, read = scan(root)
    updated = unchanged = 0
    with Session(engine) as session:
        teams = session.scalars(select(Team).where(Team.source == "rffm", Team.external_id.in_(list(crests)))).all() if crests else []
        for team in teams:
            new = crests[team.external_id]
            if team.crest_url == new:
                unchanged += 1
            else:
                team.crest_url = new
                updated += 1
        known = {t.external_id for t in teams}
        if dry_run:
            session.rollback()
        else:
            session.commit()
    return {"files": read, "codes": len(crests), "teams_updated": updated, "teams_unchanged": unchanged,
            "codes_without_team": len(set(crests) - known), "dry_run": int(dry_run)}
