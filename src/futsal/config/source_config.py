"""Configuración por fuente (`config/sources/<fuente>.json`): tabla oficial tipo de juego → deporte, compartida por el
importador, el catálogo y el alcance de ingesta. Solo lectura."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SOURCES_DIR = Path(__file__).parent / "sources"


@lru_cache(maxsize=4)
def source_config(source: str = "rffm") -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SOURCES_DIR / f"{source}.json").read_text(encoding="utf-8"))
    return data


def game_type_map(source: str = "rffm") -> dict[str, str]:
    """Código oficial de tipo de juego → modalidad (p. ej. RFFM: '1' → football11, '3' → futsal)."""
    return dict(source_config(source)["game_type_code"])
