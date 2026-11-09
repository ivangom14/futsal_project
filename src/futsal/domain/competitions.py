"""Competiciones (Fase 24): tipo (liga/copa/torneo), categoría (senior/juvenil…), alias coloquiales y filtros. Puro y determinista.

Los alias solo resuelven a competiciones que EXISTEN en el catálogo recibido: nunca se inventa un identificador. Las reglas y los
alias viven en `config/competition_aliases.json`, no en el prompt del LLM.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, NamedTuple

KIND_LABEL = {"league": "liga", "cup": "copa", "tournament": "torneo"}
MAX_LISTED = 8


def fold(text: str) -> str:
    """Minúsculas, sin acentos ni signos, espacios simples."""
    t = unicodedata.normalize("NFKD", text.lower())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in t if not unicodedata.combining(c))).strip()


def _spans(text: str, phrase: str) -> list[tuple[int, int]]:
    return [m.span() for m in re.finditer(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text)] if phrase else []


@dataclass(frozen=True)
class Competition:
    id: int
    name: str
    groups: tuple[tuple[int, str], ...] = ()  # (group_id, nombre)


class Taxonomy(NamedTuple):
    kind: str
    age: str


def classify(name: str, cfg: dict[str, Any]) -> Taxonomy:
    folded = fold(name)
    over = {fold(k): v for k, v in cfg.get("overrides", {}).items()}.get(folded)
    kind, age = cfg.get("default_kind", "league"), cfg.get("default_age", "unknown")
    for k, words in cfg["kind_from_name"].items():
        if any(_spans(folded, w) for w in words):
            kind = k
    for a, words in cfg["age_from_name"].items():
        if any(_spans(folded, w) for w in words):
            age = a
    if over:
        kind, age = over.get("kind", kind), over.get("age", age)
    return Taxonomy(kind, age)


@dataclass(frozen=True)
class Hints:
    """Lo que el usuario ha dicho sobre el ámbito: tipo, categoría, competición (alias u oficial) y grupo."""

    kind: str | None = None
    age: str | None = None
    names: frozenset[str] = field(default_factory=frozenset)  # nombres oficiales (fold) a los que apunta un alias o un nombre completo
    group: str | None = None  # nombre de grupo (fold), p. ej. «grupo 2»
    alias: str | None = None  # frase detectada (para mostrarla)
    missing: bool = False  # el alias apuntaba solo a competiciones que NO existen en el catálogo: no hay candidatas

    def empty(self) -> bool:
        return not (self.kind or self.age or self.names or self.group or self.missing)

    def describe(self) -> str:
        parts = [f"tipo={KIND_LABEL.get(self.kind or '', self.kind)}" if self.kind else "",
                 f"categoría={self.age}" if self.age else "", f"grupo={self.group}" if self.group else "",
                 f"competición/alias=«{self.alias}»" if (self.names or self.missing) and self.alias else ""]
        return ", ".join(p for p in parts if p)


def extract_hints(text: str, cfg: dict[str, Any], competitions: list[Competition]) -> Hints:
    """Pistas de UN mensaje. `names` solo contiene competiciones que existen en `competitions`."""
    low = fold(text)
    kinds = {k for k, words in cfg["kind_words"].items() if any(_spans(low, w) for w in words)}
    ages = {a for a, words in cfg["age_words"].items() if any(_spans(low, w) for w in words)}
    kind = next(iter(kinds)) if len(kinds) == 1 else None  # «liga y copa» no filtra
    age = next(iter(ages)) if len(ages) == 1 else None
    m = re.search(r"(?<![a-z0-9])grupo (\d+|unico)(?![a-z0-9])", low)
    group = f"grupo {m.group(1)}" if m else None
    existing = {fold(c.name): c for c in competitions}
    # Coincidencias (nombre oficial completo o frase de alias); solo las MÁXIMAS (no contenidas en otra más larga).
    found: list[tuple[tuple[int, int], str, list[dict[str, Any]], str]] = []
    for official in existing:
        found += [(s, official, [{"names": [official]}], official) for s in _spans(low, official)]
    for entry in cfg["aliases"]:
        for phrase in {fold(p) for p in entry["phrases"]}:
            found += [(s, phrase, [entry], phrase) for s in _spans(low, phrase)]
    maximal = [f for f in found if not any(o is not f and o[0][0] <= f[0][0] and f[0][1] <= o[0][1] and o[0] != f[0] for o in found)]
    by_span: dict[tuple[int, int], tuple[str, list[dict[str, Any]]]] = {}
    for span, phrase, entries, _ in maximal:
        by_span.setdefault(span, (phrase, []))[1].extend(entries)
    names: set[str] = set()
    alias: str | None = None
    missing = False
    for phrase, entries in by_span.values():
        chosen = entries
        if age and len(entries) > 1:
            chosen = [e for e in entries if all(classify(n, cfg).age == age for n in e["names"] if fold(n) in existing)] or []
        elif len(entries) > 1 and any(e.get("default") for e in entries):
            chosen = [e for e in entries if e.get("default")]
        resolved = {fold(n) for e in chosen for n in e["names"] if fold(n) in existing}  # descarta nombres inexistentes
        if resolved:
            names |= resolved
            alias = alias or phrase
        elif chosen and any(e is not None and e.get("phrases") for e in chosen):  # frase de alias cuyos destinos no existen
            missing, alias = True, alias or phrase
    return Hints(kind, age, frozenset(names), group, alias, missing and not names)


def merge_hints(prev: Hints, new: Hints, cfg: dict[str, Any]) -> Hints:
    """Aplica un mensaje más reciente sobre lo anterior: lo nuevo manda, lo no mencionado se conserva y un cambio de competición,
    tipo o categoría no arrastra pistas incompatibles."""
    if new.names:  # otra competición: ella determina tipo y categoría; el grupo anterior ya no vale
        return Hints(new.kind, new.age, new.names, new.group, new.alias, new.missing)
    names = prev.names
    if names and any((new.kind and classify(n, cfg).kind != new.kind) or (new.age and classify(n, cfg).age != new.age) for n in names):
        names = frozenset()  # cambio de categoría/tipo: la competición anterior ya no encaja
    if new.missing:
        return Hints(new.kind, new.age, frozenset(), new.group, new.alias, True)
    return Hints(new.kind or prev.kind, new.age or prev.age, names, new.group or prev.group, prev.alias if names else None)


def candidates(competitions: list[Competition], hints: Hints, cfg: dict[str, Any]) -> list[Competition]:
    if hints.missing:
        return []
    out = []
    for c in competitions:
        tax = classify(c.name, cfg)
        if hints.names and fold(c.name) not in hints.names:
            continue
        if (hints.kind and tax.kind != hints.kind) or (hints.age and tax.age != hints.age):
            continue
        if hints.group and not any(fold(g) == hints.group for _, g in c.groups):
            continue
        out.append(c)
    return out


def row_matches(competition: str, group: str | None, hints: Hints, cfg: dict[str, Any]) -> bool:
    """¿Una fila (competición, grupo) de find_teams cumple las pistas?"""
    tax = classify(competition, cfg)
    return not ((hints.names and fold(competition) not in hints.names) or (hints.kind and tax.kind != hints.kind)
                or (hints.age and tax.age != hints.age) or (hints.group and fold(group or "") != hints.group))


def scope_resolved(cands: list[Competition], hints: Hints) -> bool:
    """Una sola competición y, si tiene varios grupos, el grupo indicado: no hay nada más que preguntar sobre el ámbito."""
    return len(cands) == 1 and (len(cands[0].groups) <= 1 or bool(hints.group))


def _fmt(c: Competition, group: str | None) -> str:
    groups = [(gid, g) for gid, g in c.groups if not group or fold(g) == group]
    return f"«{c.name}» (competition_id={c.id}; grupos: " + ", ".join(f"«{g}» group_id={gid}" for gid, g in groups) + ")"


def scope_note(cands: list[Competition], hints: Hints, inherited: bool) -> str:
    """Nota para el LLM con lo que la aplicación ya ha resuelto (verificado con el catálogo). Vacía si no hay pistas."""
    if hints.empty():
        return ""
    head = ("\n\n[Nota de la aplicación, no escrita por el usuario; verificada con el catálogo] Filtros del usuario"
            + (" (heredados de mensajes anteriores de la conversación)" if inherited else "") + f": {hints.describe()}. ")
    if not cands:
        return head + ("Ninguna competición del catálogo los cumple: díselo con claridad (no elijas otra competición ni inventes datos) "
                       "y, si procede, ofrece alternativas reales.")
    if scope_resolved(cands, hints):
        return head + f"Competición identificada: {_fmt(cands[0], hints.group)}. Úsala sin volver a preguntar el ámbito."
    if len(cands) == 1:  # competición identificada; falta el grupo
        return head + (f"Competición identificada: {_fmt(cands[0], hints.group)}. Falta el grupo: si la pregunta es de un equipo, find_teams "
                       "lo determina; si no, pregunta cuál de estos grupos (solo esos) sin volver a preguntar la competición.")
    shown = "; ".join(_fmt(c, hints.group) for c in cands[:MAX_LISTED]) + (f"; y {len(cands) - MAX_LISTED} más" if len(cands) > MAX_LISTED else "")
    return head + (f"Competiciones posibles: {shown}. Si preguntan por un equipo, find_teams ya devuelve solo las que cumplen los filtros. "
                   "Si aún queda más de una opción, pregunta cuál mostrando SOLO esas (no incluyas las excluidas por los filtros).")
