"""Vocabulario coloquial -> vocabulario del reglamento, y referencias explícitas a circulares.

Reglas explícitas y comprobables (cada una con el motivo medido sobre los documentos indexados). No es un tesauro: solo
equivalencias que el stemmer de PostgreSQL no une y que los documentos usan de verdad. Una expansión solo AÑADE términos
alternativos a la búsqueda; nunca quita los de la pregunta y, sin ningún disparador, la consulta no cambia.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Expansion:
    trigger: tuple[str, ...]  # palabras exactas (sin acentos, minúsculas; «a|b» = cualquiera de las formas); deben aparecer TODAS
    add: tuple[str, ...]      # palabras con las que el reglamento o las circulares dicen lo mismo
    why: str


EXPANSIONS: tuple[Expansion, ...] = (
    Expansion(("portero|porteros|portera|porteras",), ("guardameta",),
              "el reglamento y las circulares dicen «guardameta»; «portero» solo aparece de pasada"),
    Expansion(("penalti|penaltis",), ("penal",),
              "las circulares dicen «tiro penal»/«penal»; el stemmer reduce «penalti» a «penalt» y no coincide con «penal»"),
    Expansion(("medio", "campo"), ("central",),
              "las Reglas FIFA dicen «línea central» donde la Circular 10 dice «línea de medio campo»"),
    Expansion(("linea", "central"), ("medio", "campo"),
              "recíproca de la anterior: la pregunta con «línea central» debe encontrar «línea de medio campo»"),
)

_WORD = re.compile(r"[a-z0-9]+")
# «circular 10», «circular n.º 10», «circular nº 27», «circular número 9»
_CIRCULAR = re.compile(r"\bcircular(?:es)?\s+(?:n(?:\.|um(?:ero)?\.?)?\s*o?\.?\s*)?(\d{1,3})\b")


def ascii_words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return _WORD.findall(folded)


def triggered(query: str) -> list[tuple[Expansion, list[str]]]:
    """Expansiones cuyos disparadores aparecen todos en la pregunta, con las palabras de la pregunta que los cumplen."""
    words = set(ascii_words(query))
    out: list[tuple[Expansion, list[str]]] = []
    for e in EXPANSIONS:
        found = [next((w for w in t.split("|") if w in words), "") for t in e.trigger]
        if all(found):
            out.append((e, found))
    return out


def cited_circulars(query: str) -> list[int]:
    """Números de circular que la pregunta cita expresamente (en orden, sin repetir)."""
    folded = unicodedata.normalize("NFKD", query).encode("ascii", "ignore").decode().lower()
    seen: list[int] = []
    for m in _CIRCULAR.finditer(folded):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen
