"""Criterios VERIFICABLES para detectar afirmaciones incompatibles con la evidencia. Funciones puras (sin red ni BD).

No se juzga el estilo del modelo: se comprueba que la respuesta no afirme nada que las tools no hayan devuelto.
- Jugadores: no puede nombrar a un jugador de la base que no aparezca en la evidencia (ni en la pregunta).
- Marcadores: en casos sin partido jugado/inexistente no puede dar un marcador.
- Tarjetas: no puede afirmar colores (el código de tarjeta no está verificado).
- Sustituciones: no puede dar minutos de cambios (no constan en el acta).
- Advertencias: si la evidencia es parcial o contradictoria, la respuesta debe decirlo.
- Ambigüedad: con varios candidatos, no puede elegir uno en silencio.
Son heurísticas léxicas deterministas: acotan la invención, no la descartan en todos los casos (ver docs/agent.md).
"""

import json
import re
import unicodedata
from typing import Any

NEG_TOKENS = frozenset({"no", "sin", "ni", "tampoco", "nunca", "ningun", "ninguna", "ninguno"})
WARN_PHRASES = ("parcial", "incomplet", "puede no", "puede que", "no consta", "sin acta", "aun no", "todavia no",
                "discrep", "difiere", "no coincide", "contradic", "no se ha importado", "no esta descargada",
                "limitacion", "provisional", "no implica", "no prueba", "no significa", "cobertura", "ojo",
                "tenga en cuenta", "ten en cuenta", "advertencia", "aviso", "no cerrad", "sin cerrar", "distint",
                "figuraba", "ha cambiado", "corrig", "actualiz", "prevalece", "segun el listado", "segun el acta")
CLARIFY_PHRASES = ("a cual", "cual de", "te refieres", "especifica", "aclara", "varios jugadores",
                   "mas de un jugador", "mas de un", "hay dos", "hay varios", "indica cual", "dime cual",
                   "podrias concretar", "concreta")
NOT_PLAYED_PHRASES = ("aun no", "todavia no", "no se ha jugado", "no se ha disputado", "no ha jugado", "no ha comenzado",
                      "programado", "pendiente", "por jugar", "no hay resultado", "sin resultado", "no ha tenido lugar")
CARD_COLORS = ("amarilla", "amarillas", "roja", "rojas")
SUB_WORDS = ("sustitu", "cambio", "entro", "entraron", "salio", "salieron", "relev")


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_CLAUSE_SPLIT = re.compile(r"[.!?\n;]+|,?\s+(?:pero|aunque|sin embargo|no obstante|si bien|y sin embargo)\s+", re.I)


def sentences(text: str) -> list[str]:
    """Frases y cláusulas: "no consta X, pero Y entró en el 12" son dos, para que la negación no tape lo inventado."""
    return [s for s in _CLAUSE_SPLIT.split(text) if s and s.strip()]


def _padded(s: str) -> str:
    return f" {norm(s)} "


def _negated(sentence: str) -> bool:
    low = norm(sentence)
    return any(t in NEG_TOKENS for t in low.split()) or "desconoc" in low


# ----------------------------------------------------------------------------- evidencia
def evidence_tokens(payloads: list[dict[str, Any]]) -> set[str]:
    """Todas las palabras (normalizadas) de todos los valores devueltos por las tools."""
    out: set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            out.update(norm(o).split())

    for p in payloads:
        walk(p)
    return out


# ----------------------------------------------------------------------------- jugadores
def _name_tokens(display_name: str) -> list[str]:
    return [t for t in norm(display_name).split() if t]


def mentioned_players(text: str, names: list[str]) -> list[str]:
    """Jugadores de `names` nombrados en `text`: todas las palabras de su nombre dentro de una misma frase."""
    sents = [set(norm(s).split()) for s in sentences(text)]
    found: list[str] = []
    for name in names:
        toks = _name_tokens(name)
        if len(toks) >= 2 and any(all(t in s for t in toks) for s in sents):
            found.append(name)
    return found


def unsupported_players(text: str, names: list[str], payloads: list[dict[str, Any]], question: str) -> list[str]:
    """Jugadores nombrados en la respuesta que NO aparecen en lo devuelto por las tools ni en la pregunta."""
    seen, asked = evidence_tokens(payloads), set(norm(question).split())
    return [n for n in mentioned_players(text, names)
            if not all(t in seen for t in _name_tokens(n)) and not all(t in asked for t in _name_tokens(n))]


# ----------------------------------------------------------------------------- cifras y vocabulario
SCORE_RE = re.compile(r"(?<![\d/:\-.])\d{1,2}\s*[-–]\s*\d{1,2}(?!\d|[/\-:.]\d)")


def has_any_score(text: str) -> bool:
    """¿Da algún marcador del tipo 3-1? (las fechas 10-10-2026 y las horas 18:30 no cuentan)."""
    return SCORE_RE.search(text) is not None


def _evidence_scores(payloads: list[dict[str, Any]]) -> set[tuple[int, int]]:
    """Marcadores presentes en lo devuelto por las tools: pares home_score/away_score y cualquier «N-M» en textos."""
    out: set[tuple[int, int]] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            h, a = o.get("home_score"), o.get("away_score")
            if isinstance(h, int) and isinstance(a, int) and not isinstance(h, bool):
                out.add((h, a))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, str):
            out.update(_parse_scores(o))

    for p in payloads:
        walk(p)
    return out


def _parse_scores(text: str) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    for m in SCORE_RE.finditer(text):
        a, b = re.split(r"\s*[-–]\s*", m.group(0))
        pairs.add((int(a), int(b)))
    return pairs


def unsupported_scores(text: str, payloads: list[dict[str, Any]], question: str = "") -> list[str]:
    """Marcadores «N-M» de la respuesta que NO figuran (en ningún sentido) en la evidencia ni en la pregunta."""
    known = _evidence_scores(payloads) | _parse_scores(question)
    known |= {(b, a) for a, b in known}
    return sorted(f"{a}-{b}" for a, b in _parse_scores(text) if (a, b) not in known)


COVERAGE_COUNT_RE = re.compile(r"\b(\d+)\s+de\s+(?:los\s+|las\s+)?(\d+)\s+(?:partidos|actas)\b", re.I)


def unsupported_coverage_counts(text: str, payloads: list[dict[str, Any]]) -> list[str]:
    """Cifras «X de Y partidos/actas» que no cuadran con `coverage` (con acta de finalizados, o los que faltan)."""
    valid: set[tuple[int, int]] = set()
    for p in payloads:
        cov = p.get("coverage")
        if isinstance(cov, dict) and isinstance(cov.get("finished_matches"), int) and isinstance(cov.get("with_report"), int):
            f, w = cov["finished_matches"], cov["with_report"]
            valid |= {(w, f), (f - w, f), (f, f)}
    if not valid:
        return []
    return [m.group(0) for m in COVERAGE_COUNT_RE.finditer(text) if (int(m.group(1)), int(m.group(2))) not in valid]

RULE_REF_RE = re.compile(r"\bregla\s+(\d{1,2})\b", re.I)
PAGE_REF_RE = re.compile(r"\b(?:páginas?|págs?\.?|pp?\.)\s*(\d{1,3})(?:\s*(?:-|–|y|a)\s*(\d{1,3}))?", re.I)
_NUMBER_RE = re.compile(r"(?<![\w/])\d+(?:[.,]\d+)?(?![\w/])")
QUOTE_RE = re.compile(r"[«\"“]([^»\"”]{20,})[»\"”]")
# «páginas 85, 90 y 177-178»: una cita con varias páginas (PAGE_REF_RE solo ve la primera pareja)
PAGE_LIST_RE = re.compile(r"\b(?:páginas?|págs?\.?|pp?\.)\s*(\d{1,3}(?:\s*(?:-|–|,|y|e|a)\s*\d{1,3})*)", re.I)


def page_numbers(text: str) -> set[int]:
    """Todas las páginas que cita el texto, también en listas y rangos."""
    return {int(n) for m in PAGE_LIST_RE.finditer(text) for n in re.findall(r"\d+", m.group(1))}


def _rule_hits(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [h for p in payloads if isinstance(p.get("hits"), list) for h in p["hits"] if isinstance(h, dict)]


def hit_pages(hit: dict[str, Any]) -> set[int]:
    nums = [int(n) for n in re.findall(r"\d+", str(hit.get("pages", "")))]
    return set(range(nums[0], nums[-1] + 1)) if nums else set()


def unsupported_rule_citations(text: str, payloads: list[dict[str, Any]]) -> list[str]:
    """Reglas o páginas citadas que no figuran en los fragmentos devueltos por `search_rules`."""
    hits = _rule_hits(payloads)
    rules = {int(h["rule"]) for h in hits if isinstance(h.get("rule"), int)}
    pages = set().union(*(hit_pages(h) for h in hits)) if hits else set()
    # «…según la Regla 3, la Regla 4…»: una cita LITERAL de un fragmento recuperado no es una cita de esas reglas por parte del modelo
    corpus = norm(" ".join(str(h.get("text", "")) for h in hits))
    unquoted = QUOTE_RE.sub(lambda m: " " if len(norm(m.group(1))) >= 20 and norm(m.group(1)) in corpus else m.group(0), text)
    out = [m.group(0) for m in RULE_REF_RE.finditer(unquoted) if int(m.group(1)) not in rules]
    for m in PAGE_LIST_RE.finditer(text):
        if not {int(n) for n in re.findall(r"\d+", m.group(1))} <= pages:  # cada página de la lista debe figurar en lo recuperado
            out.append(m.group(0))
    return out


HIT_METADATA = ("document", "doc_key", "version", "rule_title", "section", "subsection", "part")


def unsupported_digits(text: str, payloads: list[dict[str, Any]], question: str = "", metadata_only: bool = False) -> list[str]:
    """Cifras de la respuesta que no aparecen en los fragmentos recuperados ni en la pregunta (se ignoran regla y página citadas).

    Los metadatos del fragmento (título y clave del documento, versión, sección, título de la regla…) respaldan lo que se cita desde
    ellos: «Circular n.º 27», «sección 2 FINALIZACIÓN DE LOS PERIODOS». Con `metadata_only` (casos sin evidencia) el TEXTO de los
    fragmentos, aunque sea tangencial, no respalda ninguna cifra; solo la cita del propio documento.
    """
    support = " ".join((str(h.get("text", "")) if not metadata_only else "") + " " + " ".join(str(h.get(k, "")) for k in HIT_METADATA)
                       for h in _rule_hits(payloads)) + " " + question
    known = set(_NUMBER_RE.findall(support))
    cleaned = PAGE_LIST_RE.sub(" ", RULE_REF_RE.sub(" ", text))  # las páginas se validan aparte (unsupported_rule_citations)
    return sorted({n for n in _NUMBER_RE.findall(cleaned) if n not in known})


def figures_without_table(text: str, payloads: list[dict[str, Any]], question: str = "") -> list[str]:
    """Cifras que da la respuesta cuando ninguna tool devolvió filas de clasificación o goleadores (error, grupo sin tabla, sin ingerir).

    Se ignoran las que figuran en la pregunta o en lo devuelto (p. ej. la jornada que cita el aviso de la tool): lo demás es inventado.
    """
    if any(isinstance(p.get(k), list) and p[k] for p in payloads for k in ("standings", "scorers")):
        return []
    known = set(_NUMBER_RE.findall(question + " " + json.dumps(payloads, ensure_ascii=False)))
    return sorted({n for n in _NUMBER_RE.findall(text) if n not in known})


_NUMBER_WORDS = ("un", "uno", "una", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce",
                 "quince", "veinte", "treinta", "cuarenta", "cincuenta", "cien", "mil")
_QUANTITY_RE = re.compile(
    r"(?<![\w])(?:\d+(?:[.,]\d+)?|" + "|".join(_NUMBER_WORDS) + r")\s+(?:partidos?|encuentros?|jornadas?|euros?|€|sesiones|"
    r"meses|a[ñn]os|d[ií]as|semanas|multas?|sanciones)\b|€|\beuros?\b", re.I)


def invented_quantities(text: str) -> list[str]:
    """Importes o duraciones de sanción («dos partidos», «60 euros», «€») que un reglamento sin ese dato no puede respaldar."""
    return sorted({m.group(0).strip() for m in _QUANTITY_RE.finditer(text)})


def leaks_prompt(text: str, prompt: str, allowed: str | tuple[str, ...] = "", window: int = 7) -> bool:
    """¿Copia la respuesta una secuencia de `window` palabras seguidas de las instrucciones del sistema?
    `allowed` (el aviso de alcance, que sí se cita) se descuenta antes de comparar."""
    body = norm(text)
    for ok in ((allowed,) if isinstance(allowed, str) else allowed):
        if ok:
            body = body.replace(norm(ok), " ")
    words, ref = body.split(), norm(prompt).split()
    grams = {" ".join(ref[i:i + window]) for i in range(max(len(ref) - window + 1, 0))}
    return any(" ".join(words[i:i + window]) in grams for i in range(max(len(words) - window + 1, 0)))


DOMAIN_WORDS = frozenset({"jornada", "jornadas", "partido", "partidos", "equipo", "equipos", "gol", "goles", "goleador",
                          "goleadores", "resultado", "resultados", "acta", "actas", "arbitro", "arbitros", "tarjeta",
                          "tarjetas", "jugador", "jugadores", "grupo", "grupos", "marcador", "clasificacion", "clasificaciones", "regla", "reglas",
                          "reglamento", "falta", "faltas", "infraccion", "penalti", "saque"})


# Vocabulario de consultas al reglamento o a circulares/sanciones. Solo decide si se pide UNA consulta a `search_rules` antes de aceptar
# un rechazo o una limitación sin herramientas; no hace válida ninguna respuesta («FIFA» o «RFEF» solos no cuentan).
RULES_WORDS = frozenset({"regla", "reglas", "reglamento", "reglamentos", "circular", "circulares", "normativa", "sancion", "sanciones",
                         "sancionar"})


def mentions_rules(question: str) -> bool:
    """¿Parece una pregunta sobre reglamento, circulares o sanciones? (la responde `search_rules`, no los datos de competición)"""
    return any(w in RULES_WORDS for w in norm(question).split())


def mentions_competition(question: str) -> bool:
    """¿Usa vocabulario de datos de competición (partidos, equipos, clasificaciones…)? Las palabras de reglamento no cuentan."""
    return any(w in DOMAIN_WORDS and w not in RULES_WORDS for w in norm(question).split())


def mentions_domain(question: str) -> bool:
    """¿La pregunta usa vocabulario de la competición? (para no darla por ajena sin consultar las tools)"""
    return any(w in DOMAIN_WORDS for w in norm(question).split())


def card_color_claims(text: str) -> list[str]:
    """Frases que afirman un color de tarjeta sin negarlo (el significado de los códigos no está verificado)."""
    return [s.strip() for s in sentences(text)
            if any(c in norm(s).split() for c in CARD_COLORS) and not _negated(s)]


def unsupported_card_colors(text: str, payloads: list[dict[str, Any]]) -> list[str]:
    """Reglamento: el color de tarjeta es legítimo (lo dice el texto de la regla), pero debe figurar en lo recuperado."""
    support = set(norm(" ".join(str(h.get("text", "")) for h in _rule_hits(payloads))).split())
    return sorted({c for c in norm(text).split() if c in CARD_COLORS and c not in support})


def substitution_minute_claims(text: str) -> list[str]:
    """Frases que dan un minuto para un cambio/sustitución sin negarlo (no constan en el acta)."""
    return [s.strip() for s in sentences(text)
            if any(w in norm(s) for w in SUB_WORDS) and re.search(r"\b\d{1,2}\b", s) and not _negated(s)]


def says_not_played(text: str) -> bool:
    low = _padded(text)
    return any(p in low for p in NOT_PLAYED_PHRASES)


TOOL_LIMIT_PHRASES = ("mis herramientas no", "mis tools no", "las herramientas no", "no incluyen", "no incluye",
                      "no cubren", "no cubre", "no dispongo", "no tengo esa", "no tengo ese",
                      "no cuento con", "no esta en las actas",
                      "no consta en las actas", "no se almacena", "no se registra")


def states_tool_limit(text: str) -> bool:
    """¿Dice que SUS HERRAMIENTAS no cubren lo pedido, sin dar cifras ni marcadores? (limitación honesta sin tools)"""
    low = _padded(text)
    return any(p in low for p in TOOL_LIMIT_PHRASES) and not has_any_score(text) and not re.search(r"\d", text)


def clarifies(text: str) -> bool:
    low = _padded(text)
    return any(p in low for p in CLARIFY_PHRASES)


SCOPE_WORDS = frozenset({"competicion", "competiciones", "grupo", "grupos", "temporada", "temporadas", "categoria"})
REQUEST_PHRASES = ("indic", "especific", "aclar", "cual", "que competicion", "que grupo", "que temporada", "a que ", "de que ", "dime", "puedes", "podrias",
                   "podria", "necesito", "precis", "concret", "te refieres", "se refiere")
MAX_CLARIFICATION_CHARS = 600


def scope_identified(question: str, catalog: list[tuple[str, list[str]]]) -> bool:
    """¿La pregunta ya nombra una competición del catálogo y, si tiene varios grupos, también el grupo?"""
    low = f" {norm(question)} "
    return any(f" {norm(comp)} " in low and (len(groups) <= 1 or any(f" {norm(g)} " in low for g in groups))
               for comp, groups in catalog)


def asks_scope_clarification(text: str, catalog: list[tuple[str, list[str]]], question: str = "") -> bool:
    """¿Es la respuesta SOLO una petición de competición, grupo o temporada, sin datos?

    Debe usar vocabulario de ámbito (competición, grupo, temporada), formular una petición, ser breve y no contener cifras ni
    marcadores (salvo los números de los nombres del catálogo, p. ej. «Grupo 2», y los que ya figuran en la pregunta, p. ej. «jornada 2»).
    Una frase interrogativa por sí sola no basta.
    """
    if not text.strip() or len(text) > MAX_CLARIFICATION_CHARS or has_any_score(text):
        return False
    low = f" {norm(text)} "
    for name in sorted({norm(n) for c, gs in catalog for n in (c, *gs) if n}, key=len, reverse=True):
        low = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", " ", low)
    new_figures = set(re.findall(r"\d+", low)) - set(re.findall(r"\d+", question))
    return (not new_figures and any(w in SCOPE_WORDS for w in low.split())
            and any(p in low for p in REQUEST_PHRASES))


# ----------------------------------------------------------------------------- advertencias
def needs_warning(payloads: list[dict[str, Any]]) -> list[str]:
    """Motivos por los que la respuesta DEBE advertir, deducidos solo de la evidencia recuperada."""
    why: list[str] = []
    for p in payloads:
        cov = p.get("coverage")
        if isinstance(cov, dict) and cov.get("complete") is False and cov.get("finished_matches"):
            why.append("cobertura de actas parcial")
        if p.get("report_available") is False:
            why.append("acta no disponible")
        cons = p.get("consistency")
        if isinstance(cons, dict) and (cons.get("consistent") is False or cons.get("differences")):
            why.append("marcador de competición y acta contradictorios")
    return list(dict.fromkeys(why))


def answer_warns(text: str) -> bool:
    low = _padded(text)
    return any(p in low for p in WARN_PHRASES)


# Cada motivo exige SU advertencia: decir "no consta" no avisa de que la cobertura de actas es parcial.
COVERAGE_PHRASES = ("parcial", "incomplet", "cobertura", "faltan", "sin acta", "no todas", "no todos", "puede no",
                    "pendientes de acta", "falta el acta", "faltan actas")
REASON_PHRASES: dict[str, tuple[str, ...]] = {
    "cobertura de actas parcial": COVERAGE_PHRASES,
    "acta no disponible": ("sin acta", "no hay acta", "no esta descargada", "no importad", "aun no", "no disponible",
                           "no consta", "no figura", "no tengo el acta", "no se ha importado"),
    "marcador de competición y acta contradictorios": (
        "discrep", "difiere", "no coincide", "contradic", "figuraba", "ha cambiado", "corrig", "prevalece",
        "distint", "segun el listado", "segun el acta")}


def says_coverage_partial(text: str) -> bool:
    low = _padded(text)
    return any(p in low for p in COVERAGE_PHRASES)


def missing_warnings(text: str, reasons: list[str]) -> list[str]:
    """Motivos (de `needs_warning`) cuya advertencia específica NO aparece en la respuesta."""
    low = _padded(text)
    return [r for r in reasons if not any(p in low for p in REASON_PHRASES.get(r, WARN_PHRASES))]


ORDINALS = ("", "primer", "segund", "tercer", "cuart", "quint", "sext", "septim", "octav", "noven", "decim")


def claims_position(text: str, n: int) -> bool:
    """¿Afirma la respuesta que el equipo ocupa el puesto `n`? Más estricto que citar el número (los puntos también son números)."""
    low = _padded(text)  # norm convierte «2.º» en «2 o», «2º» en «2o» y «2ª» en «2a»
    return (re.search(rf"(?:puesto|posicion|lugar|plaza)\D{{0,25}}(?<!\d){n}(?!\d)", low) is not None
            or re.search(rf"(?<!\d){n}(?: o\b|[oa]\b)", low) is not None
            or (n < len(ORDINALS) and ORDINALS[n] in low) or (n == 1 and " lider" in low))
