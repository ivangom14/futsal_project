"""Resolutor de competiciones por DIMENSIONES (puro y determinista; sin E/S ni LLM).

Cada competición del catálogo se descompone, a partir de su NOMBRE OFICIAL, en tipo (liga/copa/torneo), categoría (senior/juvenil), nivel
(«preferente», «primera», «autonómica», «tercera»…), fase (previa/final) y grupos. Cada palabra del usuario fija una dimensión y las candidatas son
la INTERSECCIÓN de lo dicho (no la unión de coincidencias parciales). El contexto de la conversación nunca filtra aquí: solo se usa después, en
`agent/context.py`, como preferencia para desempatar.

Lo que el catálogo no puede decir (competiciones que no existen, palabras de referencia, señales de temporada) vive en
`config/competition_dimensions.json`; los alias coloquiales que no salen del nombre («regional») siguen en `config/competition_aliases.json`.
"""

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from futsal.domain.competitions import Competition, _spans, classify, fold

QUALIFIERS = frozenset({"previa", "final", "i", "ii"})  # distinguen fases/ediciones; solo cuentan si encajan con otra pista
AGE_NAME_TOKENS = frozenset({"senior", "juvenil"})
AGEISH = frozenset({"aficionado"})  # figura en el nombre y es dimensión de nivel, pero no cambia el «ajuste exacto»
FUZZY_CUTOFF = 0.88
MAX_ORDINAL_WORDS = 4


@dataclass(frozen=True)
class Facet:
    comp: Competition
    kind: str
    age: str
    core: frozenset[str]  # tokens del nombre sin palabras vacías
    level: frozenset[str]  # core sin palabras de tipo ni de categoría
    base: frozenset[str]  # core sin categoría ni fase: «familia» (la misma competición en otra categoría/fase)

    @property
    def family(self) -> tuple[frozenset[str], str]:
        return (self.base, self.age)  # misma competición y categoría: sus fases (previa/final) son una sola «familia»


@dataclass(frozen=True)
class Parsed:
    """Lo que UN mensaje dice sobre competición. `strong`: nombra algo concreto (nivel o alias); `weak`: solo tipo o categoría."""

    kind: str | None = None
    age: str | None = None
    level: frozenset[str] = frozenset()
    aliases: frozenset[str] | None = None  # nombres oficiales de un alias coloquial («regional»)
    group: str | None = None  # «grupo 2» (fold)
    absent: tuple[str, ...] = ()  # términos de competición/categoría que NO existen en el catálogo
    season: str | None = None  # «previous» si se pide otra temporada
    other: bool = False  # «la otra liga», «el otro grupo»
    pronoun: bool = False  # «ese equipo», «la misma»
    possessive: bool = False  # «su próximo partido»
    ordinal: int | None = None  # «la segunda» (solo vale con opciones ofrecidas; lo decide quien llama)
    content: tuple[str, ...] = ()  # palabras del mensaje (normalizadas)

    @property
    def strong(self) -> bool:
        return bool(self.level) or self.aliases is not None

    @property
    def scoped(self) -> bool:
        return self.strong or bool(self.kind or self.age or self.group)


def _singular(token: str, vocab: frozenset[str]) -> str:
    return token[:-1] if token not in vocab and token.endswith("s") and token[:-1] in vocab else token


class Resolver:
    def __init__(self, competitions: list[Competition], aliases: dict[str, Any], dims: dict[str, Any]) -> None:
        self.competitions = competitions
        self.aliases, self.dims = aliases, dims
        self.stop = frozenset(dims["stop_tokens"])
        self.kind_words = {w for ws in aliases["kind_words"].values() for w in ws}
        self.age_map = {w: a for a, ws in aliases["age_words"].items() for w in ws}
        self.kind_map = {w: k for k, ws in aliases["kind_words"].items() for w in ws}
        self.facets: list[Facet] = []
        for c in competitions:
            tax = classify(c.name, aliases)
            core = frozenset(fold(c.name).split()) - self.stop
            level = core - self.kind_words - AGE_NAME_TOKENS
            self.facets.append(Facet(c, tax.kind, tax.age, core, level, core - AGE_NAME_TOKENS - AGEISH - frozenset({"previa", "final"})))
        self.vocab = frozenset(t for f in self.facets for t in f.level)
        self.words = self.vocab | self.kind_words | set(self.age_map) | self.stop
        self.by_name = {f.comp.name: f for f in self.facets}
        self.protected = frozenset(dims.get("protected_words", ()))
        self._season = [re.compile(p) for p in dims["season_patterns"]]
        self._colloquial = self._colloquial_entries()

    # ----------------------------------------------------------------------------------------------------------------
    def _colloquial_entries(self) -> list[tuple[str, dict[str, Any]]]:
        """Alias cuyas frases NO se explican con el vocabulario de los nombres oficiales (p. ej. «regional»); el resto lo cubren las dimensiones."""
        out = []
        for entry in self.aliases["aliases"]:
            for phrase in {fold(p) for p in entry["phrases"]}:
                if any(_singular(t, self.words) not in self.words for t in phrase.split()):
                    out.append((phrase, entry))
        return sorted(out, key=lambda pe: -len(pe[0]))

    def normalize_token(self, token: str) -> str:
        """Plural y erratas evidentes («prefernte» → «preferente») hacia el vocabulario del catálogo; nunca toca ordinales de posición («tercero»)."""
        if token in self.words or token in self.protected or token.isdigit():
            return token
        token = _singular(token, self.words)
        if token in self.words or len(token) < 6:
            return token
        near = difflib.get_close_matches(token, [w for w in self.words if len(w) >= 5 and w[0] == token[0]], 1, FUZZY_CUTOFF)
        return near[0] if near else token

    # ----------------------------------------------------------------------------------------------------------------
    def parse(self, text: str) -> Parsed:
        low = fold(text)
        season = "previous" if any(p.search(low) for p in self._season) else None
        words = low.split()
        ordinals = self.dims["ordinals"]
        ordinal = next((ordinals[w] for w in words if w in ordinals), None) if len(words) <= MAX_ORDINAL_WORDS + 2 else None
        absent: list[str] = []
        for phrase in sorted(self.dims["absent_phrases"], key=len, reverse=True):
            if _spans(low, phrase):
                absent.append(phrase)
                low = re.sub(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", " ", low)
        names: set[str] | None = None
        used: set[str] = set()
        for phrase, entry in self._colloquial:
            if _spans(low, phrase):
                used.add(phrase)
                low = re.sub(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", " ", low)
                chosen = [e for e in self.aliases["aliases"] if phrase in {fold(p) for p in e["phrases"]}]
                age = next((self.age_map[w] for w in words if w in self.age_map), None)
                if age:
                    chosen = [e for e in chosen if all(classify(n, self.aliases).age == age for n in e["names"] if n in self.by_name)] or chosen
                elif len(chosen) > 1 and any(e.get("default") for e in chosen):
                    chosen = [e for e in chosen if e.get("default")]
                found = {n for e in chosen for n in e["names"] if n in self.by_name}
                names = (names or set()) | found if found else (names if names is not None else set())
        tokens = [self.normalize_token(t) for t in low.split()]
        kinds = {self.kind_map[t] for t in tokens if t in self.kind_map}
        ages = {self.age_map[t] for t in tokens if t in self.age_map}
        m = re.search(r"(?<![a-z0-9])grupo (\d+|unico)(?![a-z0-9])", low)
        level = frozenset(t for t in tokens if t in self.vocab and t not in self.stop)
        raw = set(words)
        return Parsed(
            kind=next(iter(kinds)) if len(kinds) == 1 else None, age=next(iter(ages)) if len(ages) == 1 else None, level=level,
            aliases=frozenset(names) if names is not None else None, group=f"grupo {m.group(1)}" if m else None, absent=tuple(absent), season=season,
            other=bool(raw & set(self.dims["other_words"])),
            pronoun=bool(raw & set(self.dims["pronoun_words"])) or bool(re.search(r"\beste (equipo|club|grupo)\b|\besta (liga|competicion|copa|jornada)\b", fold(text))),  # «¿cómo está…?» no es un pronombre
            possessive=bool(raw & set(self.dims["possessive_words"])), ordinal=ordinal, content=tuple(w for w in words if w not in self.stop))

    # ----------------------------------------------------------------------------------------------------------------
    def match(self, p: Parsed, *, ignore_group: bool = False) -> list[Competition]:
        """Competiciones que cumplen TODAS las dimensiones dichas (intersección). Vacío si el mensaje no dice nada de competición o se contradice."""
        if not p.scoped:
            return []
        pool = list(self.facets)
        if p.aliases is not None:
            pool = [f for f in pool if f.comp.name in p.aliases]
        if p.level:
            hard = p.level - QUALIFIERS
            narrowed = [f for f in pool if hard <= f.level and (p.level & QUALIFIERS) <= f.level]
            pool = narrowed or [f for f in pool if hard <= f.level]
        if p.kind:
            pool = [f for f in pool if f.kind == p.kind]
        if p.age:
            pool = [f for f in pool if f.age == p.age]
        if p.group and not ignore_group:
            pool = [f for f in pool if any(fold(g) == p.group for _, g in f.comp.groups)]
        if p.level and len(pool) > 1:  # ajuste exacto: «primera juvenil» es Primera Juvenil, no también «Primera División Autonómica Juvenil»
            want = p.level - QUALIFIERS - AGEISH
            tight = [f for f in pool if f.level - QUALIFIERS - AGEISH == want]
            pool = tight or pool
        return [f.comp for f in pool]

    def families(self, comps: list[Competition]) -> list[list[Competition]]:
        """Agrupa por competición+categoría (sus fases previa/final son una sola familia)."""
        out: dict[tuple[frozenset[str], str], list[Competition]] = {}
        for c in comps:
            out.setdefault(self.by_name[c.name].family, []).append(c)
        return list(out.values())

    def age_of(self, comp: Competition) -> str:
        return self.by_name[comp.name].age

    def kind_of(self, comp: Competition) -> str:
        return self.by_name[comp.name].kind

    def siblings(self, comps: list[Competition], age: str) -> list[Competition]:
        """La misma competición en otra categoría («¿Y la senior?» tras Preferente Juvenil → Preferente Aficionado). Vacío si no hay equivalente."""
        bases = {self.by_name[c.name].base for c in comps}
        return [f.comp for f in self.facets if f.base in bases and f.age == age and f.comp not in comps]

    def existing_terms(self) -> list[str]:
        return sorted(f.comp.name for f in self.facets)


@dataclass(frozen=True)
class SeasonScope:
    """Temporada pedida frente a las disponibles. Hoy solo existe la temporada en curso; añadir históricos = rellenar `available`."""

    requested: str = "current"
    available: tuple[str, ...] = field(default=("current",))

    @property
    def satisfiable(self) -> bool:
        return self.requested in self.available
