"""Deporte como dimensión de dominio (Fase 1 multideporte). Puro, sin dependencias de infraestructura.

El deporte de una competición es `competitions.modality` y SOLO lo fija el campo oficial de la fuente (`game_type_code`,
`tipojuego` en la RFFM) mediante la tabla de correspondencias de `config/sources/<fuente>.json`; nunca el nombre ni un override.
Grupos, jornadas, equipos, partidos y actas heredan el deporte de su competición.

Contrato de las llamadas: `sport` es OBLIGATORIO (`futsal` | `football11`); `all` es un ámbito explícito para consultas
combinadas intencionadas y nunca un valor por defecto. El valor por defecto vive en el frontend (fútbol sala).
"""

import re
from typing import Literal

Sport = Literal["futsal", "football11"]
SportScope = Literal["futsal", "football11", "all"]

SPORTS: tuple[str, ...] = ("futsal", "football11")  # deportes activos en Futsito
ALL = "all"
SCOPES: tuple[str, ...] = (*SPORTS, ALL)
# Modalidades que el esquema admite (la RFFM publica cinco tipos de juego); solo SPORTS están en el alcance actual.
MODALITIES: tuple[str, ...] = ("futsal", "football11", "football7", "football5", "beach")

LABELS = {"futsal": "fútbol sala", "football11": "fútbol"}


class SportError(ValueError):
    """Deporte ausente o inválido."""


def parse_scope(value: str | None) -> tuple[str, ...]:
    """Deportes que cubre un ámbito: `futsal` → (futsal,), `all` → los dos. Sin valor o desconocido → SportError."""
    if value is None or not str(value).strip():
        raise SportError(f"falta el deporte (sport); valores: {', '.join(SCOPES)}")
    v = str(value).strip().lower()
    if v == ALL:
        return SPORTS
    if v not in SPORTS:
        raise SportError(f"deporte inválido: {value!r}; valores: {', '.join(SCOPES)}")
    return (v,)


def sport_of_game_type(game_type_code: str | None, game_type_map: dict[str, str]) -> str | None:
    """Modalidad desde el código oficial de tipo de juego (None si falta o no está en la tabla de la fuente)."""
    return game_type_map.get((game_type_code or "").strip()) or None


def game_type_of_sport(sport: str, game_type_map: dict[str, str]) -> str | None:
    """Código oficial de tipo de juego de una modalidad (inverso de `sport_of_game_type`)."""
    return next((code for code, s in game_type_map.items() if s == sport), None)


# --- Deporte mencionado en el texto (determinista) ---------------------------------------------------------------------------
# «fútbol sala», «futsal» → fútbol sala; «fútbol 11/once», «F11» o «fútbol» suelto (no seguido de sala/7/5/playa) → fútbol 11.
_FUTSAL = re.compile(r"\bf[uú]tbol[\s-]*sala\b|\bfutsal\b", re.IGNORECASE)
_FOOTBALL11 = re.compile(r"\bf[uú]tbol[\s-]*(?:11|once)\b|\bf-?11\b|\bf[uú]tbol\b(?![\s-]*(?:sala|7|siete|5|cinco|playa)\b)", re.IGNORECASE)
_WITHOUT_ACCENTS = str.maketrans("áéíóúÁÉÍÓÚ", "aeiouAEIOU")


def mentioned_sports(text: str, ignore: tuple[str, ...] = ()) -> frozenset[str]:
    """Deportes que el texto nombra EXPLÍCITAMENTE. `ignore`: nombres propios del catálogo activo que contienen «fútbol»
    (p. ej. un club «ESCUELA DE FÚTBOL X» de fútbol sala) y no deben leerse como mención de deporte."""
    low = text.translate(_WITHOUT_ACCENTS).lower()
    for name in ignore:
        low = low.replace(name.translate(_WITHOUT_ACCENTS).lower(), " ")
    found = set()
    if _FUTSAL.search(low):
        found.add("futsal")
    if _FOOTBALL11.search(_FUTSAL.sub(" ", low)):
        found.add("football11")
    return frozenset(found)
