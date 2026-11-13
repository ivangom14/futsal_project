"""Índice de las copias locales de escudos: carpeta, nombre de archivo válido y `index.json` (ruta RFFM → archivo).

Módulo ligero (sin Pillow ni base de datos) para que la API y el servicio de chat lo usen sin cargar la descarga.
"""

import json
import os
import re
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path("data/crests")
INDEX = "index.json"
FILE_RE = re.compile(r"^[0-9a-f]{20}\.webp$")


def crests_dir() -> Path:
    """Carpeta de las copias (CRESTS_DIR o `data/crests`), compartida por la CLI, la API y el servicio de chat."""
    return Path(os.environ.get("CRESTS_DIR") or DEFAULT_DIR)


def load_index(folder: Path) -> dict[str, dict[str, Any]]:
    """{ruta RFFM: {file, bytes_source, bytes, fetched_at}}; entradas con un nombre de archivo no válido se descartan."""
    try:
        data = json.loads((folder / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and FILE_RE.match(str(v.get("file", "")))}
