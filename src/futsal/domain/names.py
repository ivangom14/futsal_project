"""Normalización de nombres para la API v2 (puro, sin E/S): jugadores, árbitros y clubes."""

import re
import unicodedata

_TOKEN = re.compile(r"[a-z0-9]+")
# Términos genéricos que no identifican al club («CLUB», «C.F.S.», «F.S.»...). Los prefijos AD/UD/SD NO se quitan: separan clubes distintos.
GENERIC_CLUB_TOKENS = frozenset({"club", "cfs", "fs", "cf", "cd", "futsal"})
SQUAD_SUFFIXES = frozenset({"a", "b", "c"})


def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in plain if not unicodedata.combining(c))


def tokens_of(text: str) -> list[str]:
    """Palabras normalizadas (minúsculas, sin acentos ni signos). Solo `[a-z0-9]`: nunca contiene caracteres peligrosos."""
    return _TOKEN.findall(_fold(text))


def norm_name(text: str) -> str:
    return " ".join(tokens_of(text))


def _club_tokens(name: str) -> list[str]:
    return tokens_of(_fold(name).replace(".", ""))  # «C.F.S.» -> «cfs»


def _drop_futbol_sala(tokens: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] == "futbol" and tokens[i + 1:i + 2] == ["sala"]:
            i += 2
            continue
        out.append(tokens[i])
        i += 1
    return out


def club_base_tokens(name: str) -> list[str]:
    """Nombre base de un equipo: sin signos, sin «club»/«C.F.S.»/«F.S.»… y sin sufijo de equipo A/B/C."""
    raw = _club_tokens(name)
    toks = _drop_futbol_sala([t for t in raw if t not in GENERIC_CLUB_TOKENS])
    if len(toks) > 1 and toks[-1] in SQUAD_SUFFIXES:
        toks = toks[:-1]
    return toks or raw


def club_base_name(name: str) -> str:
    return " ".join(club_base_tokens(name))


def squad_of(name: str) -> str | None:
    """Letra de equipo (A/B/C) si el nombre acaba en ella; `None` si no lleva sufijo."""
    raw = _drop_futbol_sala([t for t in _club_tokens(name) if t not in GENERIC_CLUB_TOKENS])
    return raw[-1].upper() if len(raw) > 1 and raw[-1] in SQUAD_SUFFIXES else None
