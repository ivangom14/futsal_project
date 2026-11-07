"""Detección CONSERVADORA de un equipo en la pregunta, apoyada en el catálogo real (Fase 3A: prefetch de entidades).

Principio: es preferible no detectar nada (el flujo sigue como hoy) que detectar mal. Por eso NO hay coincidencias aproximadas ni por subcadena:
- Se normalizan comillas, números, guiones, puntos de siglas («C.D.», «A. D.»), espacios y mayúsculas, tanto en la pregunta como en los nombres del catálogo.
- Solo se acepta que la pregunta contenga, con límites de palabra, el nombre COMPLETO de un equipo o su «núcleo» (el nombre sin la forma jurídica inicial: «C.D. DOSA» → «dosa»).
  Un núcleo de una sola palabra exige que esa palabra pertenezca a un único nombre del catálogo y que no sea una palabra genérica.
- Se abstiene (no devuelve equipo) ante: ningún equipo, varios equipos distintos, homónimos (el mismo nombre en varias competiciones/grupos) que el ámbito dicho
  en la pregunta no desambigua, preguntas de reglamento, fechas o temporadas históricas, y conversaciones con contexto previo.
- La desambiguación por ámbito reutiliza `domain.competitions.row_matches` (la misma lógica que filtra `find_teams` en el agente): no hay una segunda.

Módulo puro: sin E/S ni LLM. El catálogo lo construye el agente con `list_teams` (o los tests con un fixture).
"""

import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from futsal.agent.criteria import DOMAIN_WORDS, mentions_rules
from futsal.domain.competitions import Hints, row_matches

LEGAL_FORMS = frozenset({"cde", "cd", "cdb", "cf", "cfs", "ad", "ud", "efs", "fs", "sd", "ef", "club"})  # forma jurídica inicial, no distintiva
GENERIC = frozenset(DOMAIN_WORDS) | frozenset({"equipo", "equipos", "liga", "copa", "senior", "juvenil", "sala", "futsal", "futbol", "norte", "sur", "este", "oeste",
                                               "deportivo", "deportiva", "colegio", "escuela", "union", "ciudad", "villa", "atletico", "sporting", "real"})
STANDINGS_WORDS = frozenset({"clasificacion", "posicion", "puesto", "puntos", "tabla", "lider", "clasificado", "clasificada"})
# Cualquiera de estas palabras indica que la pregunta puede necesitar partidos, actas, goleadores o fechas: entonces NO se omite el prefetch.
NOT_STANDINGS_WORDS = frozenset({
    "resultado", "resultados", "partido", "partidos", "jugo", "juega", "jugara", "jugado", "jugados", "gano", "ganado", "ganados", "perdio", "perdido", "perdidos",
    "empato", "empatado", "empatados", "empates", "victoria", "victorias", "derrota", "derrotas", "gol", "goles", "goleador", "goleadores", "marco", "marcado",
    "proximo", "proxima", "ultimo", "ultima", "ultimos", "ultimas", "anterior", "siguiente", "jornada", "jornadas", "acta", "actas", "contra", "vs", "rival", "rivales",
    "enfrentamiento", "campo", "pista", "horario", "hora", "fecha", "cuando", "donde", "arbitro", "arbitros", "tarjeta", "tarjetas", "amarilla", "roja", "racha", "forma",
    "jugador", "jugadores", "entrenador", "recuento", "mejor", "peor", "comparar", "compara", "comparacion", "ademas", "tambien"})
MIN_CORE_CHARS = 4
MAX_NGRAM = 9
_DOTTED = re.compile(r"(?<![a-z0-9])(?:[a-z]\.\s?){2,}")  # c.d.e. · a. d. · e.f.s.
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)|\b(?:temporada|ano|año)s?\s+(?:pasad[ao]s?|anterior(?:es)?)\b|\bhace\s+\w+\s+anos?\b")
_QUOTES = str.maketrans({c: " " for c in "'\"‘’“”´`‚‛"})
_APOSTROPHE_FORMS = str.maketrans({"\u2018": "'", "\u2019": "'", "´": "'", "`": "'", "\u201a": "'", "\u201b": "'"})
_INNER_APOSTROPHE = re.compile(r"(?<=[a-z])'(?=[a-z])")


def normalize(text: str) -> str:
    """Minúsculas, sin acentos, comillas/guiones/puntuación fuera, siglas con puntos unidas («A. D.» → «ad») y espacios simples."""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c)).replace("ñ", "n")
    t = t.translate(_APOSTROPHE_FORMS).replace("\u2019", "'")
    t = _INNER_APOSTROPHE.sub("", t)  # «P'ALPIE» y «PALPIE» son la misma palabra; el apóstrofo de «'B'» (letra suelta) sí separa
    t = t.translate(_QUOTES)
    t = _DOTTED.sub(lambda m: re.sub(r"[.\s]", "", m.group(0)) + " ", t)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


@dataclass(frozen=True)
class TeamEntry:
    name: str  # nombre oficial tal cual figura en la base (es lo que se pasa a find_teams)
    competition: str
    group: str
    group_ref: str  # id del grupo (en tests, una referencia)


@dataclass(frozen=True)
class Decision:
    fired: bool
    reason: str  # ok | no_match | multi_team | homonym | ambiguous_form | rules | historical | context | too_generic
    team: str | None = None  # nombre oficial del equipo detectado
    span: str | None = None  # lo que el usuario escribió (normalizado)
    scoped: bool = False  # era homónimo y el ámbito de la pregunta lo desambiguó
    candidates: tuple[str, ...] = field(default_factory=tuple)


class TeamCatalog:
    """Índice de nombres del catálogo real: forma normalizada → equipos. Inmutable tras construirse."""

    def __init__(self, entries: list[TeamEntry]) -> None:
        self.entries = entries
        self.by_name: dict[str, list[TeamEntry]] = defaultdict(list)
        for e in entries:
            self.by_name[e.name].append(e)
        self.forms: dict[tuple[str, ...], set[str]] = defaultdict(set)  # n-grama normalizado -> nombres oficiales
        self.core_forms: set[tuple[str, ...]] = set()
        token_names: dict[str, set[str]] = defaultdict(set)
        for name in self.by_name:
            toks = normalize(name).split()
            if not toks:
                continue
            self.forms[tuple(toks)].add(name)
            for t in toks:
                token_names[t].add(name)
            core = list(toks)
            while len(core) > 1 and core[0] in LEGAL_FORMS:
                core.pop(0)
            if tuple(core) != tuple(toks):
                self.forms[tuple(core)].add(name)
                self.core_forms.add(tuple(core))
        self.token_names = token_names

    @classmethod
    def from_json(cls, path: Path) -> "TeamCatalog":
        data = json.loads(path.read_text(encoding="utf-8"))["entries"]
        return cls([TeamEntry(e["name"], e["competition"], e["group"], e["group_ref"]) for e in data])

    @classmethod
    def from_groups(cls, groups: list[tuple[str, str, str, list[str]]]) -> "TeamCatalog":
        """(competición, grupo, id del grupo, nombres de equipo) -> catálogo. Lo usa el agente con los datos de `list_teams`."""
        return cls([TeamEntry(n, comp, grp, ref) for comp, grp, ref, names in groups for n in names])

    def homonym(self, name: str) -> bool:
        return len({e.group_ref for e in self.by_name[name]}) > 1


def _matches(tokens: list[str], catalog: TeamCatalog) -> list[tuple[int, int, tuple[str, ...], set[str]]]:
    found = []
    for i in range(len(tokens)):
        for n in range(1, min(MAX_NGRAM, len(tokens) - i) + 1):
            form = tuple(tokens[i:i + n])
            names = catalog.forms.get(form)
            if names:
                found.append((i, i + n, form, names))
    # solo las máximas: una coincidencia contenida en otra más larga no cuenta
    return [m for m in found if not any(o is not m and o[0] <= m[0] and m[1] <= o[1] and (o[1] - o[0]) > (m[1] - m[0]) for o in found)]


def scan(tokens: list[str], catalog: TeamCatalog) -> tuple[dict[str, tuple[str, ...]], bool, bool]:
    """Equipos que los `tokens` nombran sin duda: (nombre oficial -> forma escrita, ¿hubo una forma que identifica a varios equipos?, ¿solo palabras genéricas?).
    Es el bucle de `decide`, sin cambios, extraído para que el resolutor de contexto reutilice EXACTAMENTE la misma regla de detección."""
    chosen: dict[str, tuple[str, ...]] = {}
    ambiguous_form = False
    generic_only = False
    for _, _, form, names in _matches(tokens, catalog):
        is_full = any(tuple(normalize(n).split()) == form for n in names)
        if len(names) > 1:  # la misma forma identifica a varios equipos distintos
            ambiguous_form = True
            continue
        (name,) = names
        if not is_full:  # solo el núcleo: exige una palabra distintiva, no genérica y propia de un único nombre
            if len(form) == 1 and (len(form[0]) < MIN_CORE_CHARS or form[0] in GENERIC or len(catalog.token_names.get(form[0], ())) != 1):
                generic_only = True
                continue
            if len(form) > 1 and all(t in GENERIC or len(t) < MIN_CORE_CHARS for t in form):
                generic_only = True
                continue
        chosen[name] = form
    return chosen, ambiguous_form, generic_only


def standings_only(question: str) -> bool:
    """Pregunta de clasificación pura (posición, puntos, tabla) sin ninguna señal de partidos, fechas, goleadores o preguntas compuestas.
    Capa APARTE de `decide` (que no cambia: sus métricas selladas en el Holdout A siguen valiendo): se aplica antes. El prefetch no aporta nada ahí (la herramienta de clasificación se pide por grupo) y solo añade tokens; omitirlo devuelve el comportamiento de siempre."""
    words = set(normalize(question).split())
    return bool(words & STANDINGS_WORDS) and not words & NOT_STANDINGS_WORDS


def decide(question: str, catalog: TeamCatalog, hints: Hints | None = None, aliases: dict[str, Any] | None = None,
           has_context: bool = False) -> Decision:
    """¿Hay UN equipo inequívoco en la pregunta? Si hay la menor duda, `fired=False` con el motivo."""
    if has_context:
        return Decision(False, "context")
    if mentions_rules(question):
        return Decision(False, "rules")
    if _YEAR.search(normalize(question)) or _YEAR.search(question.lower()):
        return Decision(False, "historical")
    tokens = normalize(question).split()
    chosen, ambiguous_form, generic_only = scan(tokens, catalog)
    if not chosen:
        return Decision(False, "ambiguous_form" if ambiguous_form else "too_generic" if generic_only else "no_match")
    if len(chosen) > 1 or ambiguous_form:
        return Decision(False, "multi_team" if len(chosen) > 1 else "ambiguous_form", candidates=tuple(sorted(chosen)))
    (team, form), = chosen.items()
    entries = catalog.by_name[team]
    scoped = False
    if catalog.homonym(team):  # el mismo nombre en varios grupos: solo si el ámbito dicho en la pregunta deja exactamente uno
        rows = [e for e in entries if row_matches(e.competition, e.group, hints, aliases or {})] if hints is not None and not hints.empty() and aliases else []
        if len({e.group_ref for e in rows}) != 1:
            return Decision(False, "homonym", team, " ".join(form))
        scoped = True
    return Decision(True, "ok", team, " ".join(form), scoped)
