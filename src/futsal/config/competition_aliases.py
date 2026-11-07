"""Carga de `competition_aliases.json` (configuración versionada de alias y reglas de clasificación de competiciones)."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).with_name("competition_aliases.json")
REQUIRED = ("kind_words", "age_words", "kind_from_name", "age_from_name", "aliases")


@lru_cache(maxsize=1)
def load_aliases() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise ValueError(f"competition_aliases.json: faltan claves {missing}")
    return data


DIMENSIONS_PATH = Path(__file__).with_name("competition_dimensions.json")


@lru_cache(maxsize=1)
def load_dimensions() -> dict[str, Any]:
    """Vocabulario del resolutor por dimensiones (`domain/resolver.py`)."""
    data: dict[str, Any] = json.loads(DIMENSIONS_PATH.read_text(encoding="utf-8"))
    missing = [k for k in ("stop_tokens", "absent_phrases", "ordinals", "season_patterns") if k not in data]
    if missing:
        raise ValueError(f"competition_dimensions.json: faltan claves {missing}")
    return data
