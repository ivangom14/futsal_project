"""Calificación determinista por turno (docs/v2/evaluation.md §5). Sin juez LLM: compara HECHOS, no frases.

Veredictos: `correct`, `partial` (conducta correcta pero falta un hecho: cuenta como fallo en la tasa principal), `fail` (conducta
incorrecta) y `critical`. Clase de error obligatoria: `critical` (competición/grupo/categoría/temporada equivocada, dato inventado,
incompatible con la fuente oficial, cita no recuperada), `interpretation` (conducta errónea: responde cuando debía aclarar o al
revés, intención mal entendida, herramientas necesarias no usadas) y `presentation` (conducta correcta pero hecho requerido
ausente, respuesta confusa o incompleta). Reutiliza los detectores de `agent.criteria` y `agent.evaluate`.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from typing import Any

from futsal.agent.agent import OFF_TOPIC_MESSAGE
from futsal.agent.criteria import (
    REQUEST_PHRASES,
    _negated,
    clarifies,
    says_not_played,
    sentences,
    states_tool_limit,
)
from futsal.agent.evaluate import (
    LIMIT_PHRASES,
    EvalCase,
    Fact,
    Truth,
    answer_ok,
    has_date,
    has_number,
    has_score,
    invention_claims,
    key,
    norm,
    presentation_ok,
    rule_answer_issue,
)

CRITICAL, INTERPRETATION, PRESENTATION = "critical", "interpretation", "presentation"
VERDICT_ORDER = {"correct": 0, "partial": 1, "fail": 2, "critical": 3}
ASK_WORDS = ("quieres", "prefieres", "te interesa", "que equipo", "de cual", "cual de", "cuales")


@dataclass
class TurnGrade:
    verdict: str
    error_class: str | None = None
    reasons: list[str] = field(default_factory=list)
    behavior_detected: list[str] = field(default_factory=list)
    facts_required: int = 0
    facts_present: list[str] = field(default_factory=list)
    facts_missing: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)
    contamination: list[str] = field(default_factory=list)
    tools_missing: list[str] = field(default_factory=list)
    tools_unexpected: list[str] = field(default_factory=list)
    over_budget: bool = False
    needs_human_review: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


# ----------------------------------------------------------------------------- conducta
def is_refusal(text: str) -> bool:
    return text.strip() == OFF_TOPIC_MESSAGE or "solo puedo responder" in norm(text)


def is_clarification(text: str) -> bool:
    low = norm(text)
    return clarifies(text) or ("?" in text and (any(p in low for p in REQUEST_PHRASES) or any(p in low for p in ASK_WORDS)))


def is_abstention(text: str) -> bool:
    low = norm(text)
    return (any(p in low for p in LIMIT_PHRASES) or states_tool_limit(text) or says_not_played(text)) and not is_refusal(text)


def detect_behavior(text: str) -> list[str]:
    out = []
    if is_refusal(text):
        out.append("offtopic")
    if is_clarification(text):
        out.append("clarify")
    if is_abstention(text):
        out.append("abstain")
    return out or ["answer"]


# ----------------------------------------------------------------------------- hechos
def _label_pattern(label: str) -> str:
    words = [re.escape(w.rstrip("s")) + r"\w*" for w in norm(label).split() if len(w) > 3]  # raíz: «punto» casa con «puntos»
    return "|".join(words) if words else "$^"


def _low(text: str) -> str:
    """Minúsculas y sin acentos, CONSERVANDO la puntuación (los decimales «7,38» no se parten)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def number_with_label(text: str, n: str, label: str) -> bool:
    """¿Aparece la cifra `n` pegada a su etiqueta («12 puntos», «puntos: 12»)?"""
    low, lab = _low(text), _label_pattern(label)
    num = re.escape(n).replace(",", "[,.]").replace(r"\.", "[,.]")
    return bool(re.search(rf"(?<![\d,.]){num}(?![\d,]|\.\d)\s*(?:\w+\s+){{0,1}}(?:{lab})", low)
                or re.search(rf"(?:{lab})(?:\s+\w+){{0,2}}\s*[:=]?\s*(?<![\d,.]){num}(?![\d,]|\.\d)", low))


def _numbers_with_label(text: str, label: str) -> set[str]:
    low, lab = _low(text), _label_pattern(label)
    found = set(re.findall(rf"(?<![\d,.])(\d{{1,3}}(?:[,.]\d+)?)(?![\d,]|\.\d)\s*(?:\w+\s+){{0,1}}(?:{lab})", low))
    found |= set(re.findall(rf"(?:{lab})(?:\s+\w+){{0,2}}\s*[:=]?\s*(?<![\d,.])(\d{{1,3}}(?:[,.]\d+)?)(?![\d,]|\.\d)", low))
    return found


def _claims_pos(text: str, n: int, team: str = "") -> bool:
    low = f" {norm(text)} "
    near = re.search(rf"(?:puesto|posicion|lugar|plaza)\D{{0,25}}(?<!\d){n}(?!\d)", low) is not None \
        or re.search(rf"(?<!\d){n}(?: o\b|[oa]\b)", low) is not None or (n == 1 and " lider" in low)
    return near and (not team or key(team) in low)


def _present(f: dict[str, Any], text: str) -> bool:
    """¿Figura el hecho `f` en la respuesta?"""
    kind, value, low = f["kind"], f.get("value"), norm(text)
    if kind == "text_any":
        return any(norm(str(a)) in low for a in f.get("any", []))
    if kind == "names":
        vals = value if isinstance(value, list) else [value]
        return all(key(str(v)) in low or norm(str(v)) in low for v in vals)
    if kind == "name":
        return key(str(value)) in low or norm(str(value)) in low
    if kind == "date":
        return has_date(text, date.fromisoformat(str(value)))
    if kind == "score":
        h, a = str(value).split("-")
        return has_score(text, int(h), int(a))
    if kind == "position":
        return _claims_pos(text, int(str(value)), str(f.get("team", "")))
    if kind == "number":
        sval = str(value)
        if re.fullmatch(r"\d+", sval):
            return number_with_label(text, sval, str(f.get("label", ""))) if f.get("strict") else has_number(text, int(sval))
        return re.search(rf"(?<![\d,.]){re.escape(sval).replace(',', '[,.]')}(?!\d)", text) is not None
    return False


def _label(f: dict[str, Any]) -> str:
    return str(f.get("label") or f.get("value") or f.get("any"))


def _name_values(f: dict[str, Any]) -> list[str]:
    value = f.get("value")
    return [str(v) for v in (value if isinstance(value, list) else [value])] if f["kind"] in ("name", "names") else []


def _mask_longer_decoys(text: str, required: list[dict[str, Any]], decoys: list[dict[str, Any]]) -> str:
    """Texto normalizado sin los nombres de señuelos que CONTIENEN un nombre pedido («C.D. DOSA ZONA 3» contiene «C.D. DOSA»):
    nombrar al señuelo no cuenta como nombrar al equipo pedido."""
    low = norm(text)
    wanted = {norm(v) for f in required for v in _name_values(f)}
    for d in decoys:
        for v in _name_values(d):
            nv = norm(v)
            if nv and any(r in nv and r != nv for r in wanted):
                low = low.replace(nv, " ")
    return low


def _decoy_hit(f: dict[str, Any], text: str, required: list[dict[str, Any]] | None = None) -> bool:
    """El hecho de un señuelo aparece EN LA RESPUESTA (cifras solo pegadas a su etiqueta; nombres completos, no su ficha distintiva).
    Un nombre de señuelo contenido en un nombre pedido más largo («C.D. DOSA» en «C.D. DOSA ZONA 3») no cuenta."""
    if f["kind"] == "number":
        sval = str(f["value"])
        return bool(f.get("label")) and number_with_label(text, sval, str(f["label"]))
    if f["kind"] in ("name", "names"):
        low = norm(text)
        for r in required or []:
            for v in _name_values(r):
                nv = norm(v)
                if any(norm(d) in nv and norm(d) != nv for d in _name_values(f)):
                    low = low.replace(nv, " ")
        return all(norm(v) in low for v in _name_values(f))
    return _present(f, text)


def _wrong_values(required: list[dict[str, Any]], text: str) -> list[str]:
    """Cifras o marcadores con la etiqueta pedida pero DISTINTOS del dato oficial (incompatible con la fuente)."""
    out: list[str] = []
    numbers = [f for f in required if f["kind"] == "number" and f.get("label") and not f.get("lenient")]
    for f in numbers:
        siblings = [g for g in numbers if g.get("label") == f.get("label")]
        if len(siblings) != 1 or _present(f, text):
            continue
        said = _numbers_with_label(text, str(f["label"]))
        if said and not (said & {str(f["value"]), str(f["value"]).replace(",", ".")}):
            out.append(f"da {', '.join(sorted(said))} como «{f['label']}» pero el dato oficial es {f['value']}")
    scores = [f for f in required if f["kind"] == "score" and not f.get("lenient")]
    if len(scores) == 1 and not _present(scores[0], text):
        from futsal.agent.criteria import SCORE_RE
        given = SCORE_RE.findall(text)
        if given:
            out.append(f"da el marcador {given[0].replace(' ', '')} pero el oficial es {scores[0]['value']}")
    return out


def _non_negated(text: str, needle: str) -> bool:
    n = norm(needle)
    return any(n in norm(s) and not _negated(s) for s in sentences(text))


def _score_non_negated(text: str, score: str) -> bool:
    h, a = (int(x) for x in score.split("-"))
    return any(has_score(s, h, a) and not _negated(s) for s in sentences(text))


# ----------------------------------------------------------------------------- nombres inventados (equipos, jugadores, competiciones)
CONNECTORS = frozenset({"de", "del", "la", "las", "los", "el", "y", "da", "do"})
STARTERS = frozenset({"el", "la", "los", "las", "un", "una", "unos", "unas", "en", "segun", "hay", "dos", "tres", "no", "si", "es", "su", "sus",
                      "ese", "esa", "este", "esta", "tiene", "tambien", "ademas", "ahora", "ayer", "hoy", "cuando", "donde", "contra", "ante",
                      "por", "para", "con", "sin", "sobre", "entre", "desde", "hasta", "como", "que", "quien", "cual", "cuales", "al", "lo"})
GENERIC = frozenset({
    "regla", "reglas", "circular", "jornada", "grupo", "primera", "segunda", "tercera", "liga", "copa", "temporada", "federacion", "reglamento",
    "fifa", "rfef", "rffm", "futsal", "futbol", "sala", "competicion", "division", "autonomica", "aficionado", "preferente", "juvenil", "senior",
    "femenina", "femenino", "cadete", "clasificacion", "oficial", "resumen", "partido", "partidos", "resultado", "resultados", "goles", "gol",
    "puntos", "equipo", "equipos", "datos", "informacion", "tabla", "goleadores", "goleador", "acta", "arbitro", "arbitros", "tarjetas",
    "proximo", "ultimo", "siguiente", "anterior", "fuente", "nota", "importante", "total", "fuera", "casa", "local", "visitante", "madrid",
    "comunidad", "fase", "final", "previa", "campeonato", "posicion", "puesto", "lider", "colista", "lunes", "martes", "miercoles", "jueves",
    "viernes", "sabado", "domingo", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
    "noviembre", "diciembre", "solo", "puedo", "responder", "jugo", "marco", "gano", "perdio", "empato", "juega", "jugara"})
_CAP = re.compile(r"^[A-ZÁÉÍÓÚÑÜ]")
_ABBR = re.compile(r"^(?:[A-Za-z]\.){2,}$")


def _strip(word: str) -> str:
    w = word.strip(",;:!?()[]«»\"“”¿¡")
    return w if _ABBR.match(w) else w.rstrip(".")


def _name_runs(text: str) -> list[list[str]]:
    """Secuencias de palabras con mayúscula inicial (con conectores «de», «del»…): candidatos a nombre de equipo, jugador o competición.
    La mayúscula inicial de una frase no informa: se descarta la primera palabra de cada frase."""
    runs: list[list[str]] = []
    cur: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        nonlocal cur, pending
        while cur and norm(cur[0]) in STARTERS:
            cur = cur[1:]
        while cur and (norm(cur[-1]) in STARTERS or norm(cur[-1]) in CONNECTORS):
            cur = cur[:-1]
        if sum(bool(_CAP.match(w)) for w in cur) >= 2:
            runs.append(cur)
        cur, pending = [], []

    sentence_start = True
    for raw in text.split():
        w = _strip(raw)
        cap = bool(w) and bool(_CAP.match(w))
        if cap and sentence_start:
            flush()
        elif cap:
            cur += [*pending, w]
            pending = []
        elif cur and norm(w) in CONNECTORS:
            pending.append(w)
        else:
            flush()
        brk = raw[-1:] in ",;:.!?" and not _ABBR.match(w)  # una coma o un punto cierran el nombre («GARCIA, JAVIER; CASTRO, SERGIO»)
        if brk:
            flush()
        sentence_start = brk and raw[-1:] in ".!?:;"
    flush()
    return runs


def _strings(o: Any, out: list[str]) -> None:
    if isinstance(o, dict):
        for v in o.values():
            _strings(v, out)
    elif isinstance(o, list):
        for v in o:
            _strings(v, out)
    elif isinstance(o, str):
        out.append(o)


def _toks(text: str) -> frozenset[str]:
    return frozenset(t for t in norm(text).split() if t not in CONNECTORS)


def unknown_names(turn: Any, answer: str, payloads: list[dict[str, Any]], catalog_names: Sequence[str]) -> tuple[list[str], list[str]]:
    """(críticos, a revisar). Un nombre con forma de equipo, jugador o competición debe figurar en la evidencia del turno, en la verdad del caso
    o en la pregunta (se compara sin acentos ni mayúsculas, por palabras). Si existe en el snapshot pero no en lo anterior, es de otra entidad;
    si no existe en ninguna parte, está inventado. Lo que no se puede decidir con seguridad va a revisión humana."""
    evidence: list[str] = []
    _strings(payloads, evidence)
    case_known = [_toks(s) for s in [turn.user, *turn.known_names, *evidence]]
    catalog = [_toks(s) for s in catalog_names]
    critical: list[str] = []
    review: list[str] = []
    for run in _name_runs(answer):
        cand = _toks(" ".join(run))
        shown = " ".join(run)
        if not cand or any(cand <= k for k in case_known):
            continue
        if any(cand <= k for k in catalog):
            critical.append(f"nombra a «{shown}», que existe en el snapshot pero no figura en la evidencia ni en la verdad del caso (otra entidad)")
        elif not cand <= GENERIC:
            critical.append(f"nombre inventado: «{shown}» no figura en la evidencia, en el snapshot ni en la pregunta")
    for raw in re.findall(r"\b[A-ZÁÉÍÓÚÑÜ]{6,}\b", answer):
        t = norm(raw)
        if t not in GENERIC and not any(t in k for k in case_known) and not any(t in k for k in catalog) and not any(raw in r for r in critical):
            review.append(f"palabra en mayúsculas «{raw}» que no se puede contrastar con la evidencia ni con el snapshot")
    return critical, review


# ----------------------------------------------------------------------------- reglamento (casos actuales, tal cual)
@lru_cache(maxsize=1)
def rule_case_map() -> dict[str, EvalCase]:
    from futsal.agent.rule_cases import rule_cases
    return {c.id: c for c in rule_cases()}


def _shim_truth(players: list[str]) -> Truth:
    return Truth(group_id=0, teams={}, rounds={}, matches=[], player_names=players)


# ----------------------------------------------------------------------------- calificación
def grade_turn(turn: Any, answer: str, tools_used: list[str], payloads: list[dict[str, Any]], llm_calls: int,
               players: list[str], names: Sequence[str] = ()) -> TurnGrade:
    g = TurnGrade("correct")
    low = norm(answer)
    g.behavior_detected = detect_behavior(answer)
    g.over_budget = turn.max_llm_calls is not None and llm_calls > turn.max_llm_calls
    rule = rule_case_map().get(turn.rule_case) if turn.rule_case else None
    crit: list[str] = []
    interp: list[str] = []
    pres: list[str] = []

    # --- hechos requeridos
    required = turn.facts
    masked = ""
    if rule is not None:
        g.facts_required = len(rule.facts or [])
        for i, f in enumerate(rule.facts or []):
            (g.facts_present if answer_ok(f, answer) else g.facts_missing).append(f"{f.kind}:{f.label}#{i}")
    else:
        g.facts_required = len(required)
        masked = _mask_longer_decoys(answer, required, turn.decoy_facts)
        for f in required:
            (g.facts_present if _present(f, masked if f["kind"] in ("name", "names") else answer) else g.facts_missing).append(_label(f))

    # --- detectores de invención (los de la evaluación actual, tal cual)
    rules_facts = ([Fact("rule_cited", "v1")] if turn.behavior == "answer" else [Fact("no_evidence_rules", "v1")]) \
        if turn.source == "rules" else []
    case = rule or EvalCase("v1", "tablas_v1" if turn.source == "official_table" else "v1", turn.user, rules_facts,
                            forbid_scores=turn.forbid_any_score)
    g.claims = invention_claims(case, answer, _shim_truth(players), payloads)
    crit += [f"dato inventado: {c}" for c in g.claims]

    # --- contaminación: hechos de homónimos, otras categorías o temporadas
    for d in turn.decoy_facts:
        if not _decoy_hit(d, answer, required):
            continue
        counterpart = [f for f in required if f["kind"] == d["kind"] and f.get("label") == d.get("label")] or required
        label = f"{_label(d)} de {d.get('decoy_of', 'otra entidad')}"
        if any(_present(f, masked if f["kind"] in ("name", "names") else answer) for f in counterpart):
            g.contamination.append(f"{label}, mezclado con el dato pedido")
        else:
            g.contamination.append(label)
    crit += [f"contaminación: da el hecho de otra entidad como el pedido ({c})" for c in g.contamination]
    hit_forbidden = [_label(f) for f in turn.forbid_facts if _decoy_hit(f, answer)]
    if hit_forbidden:
        clarified = turn.behavior == "clarify" and is_clarification(answer)
        (pres if clarified else crit).append(("responde además con datos de un candidato: " if clarified else "da datos sin aclarar: ")
                                             + ", ".join(hit_forbidden[:4]))
        if not clarified:
            g.contamination += hit_forbidden
    if turn.behavior == "answer":
        crit += [f"incompatible con la fuente oficial: {w}" for w in _wrong_values(required, answer)]
        crit += [f"ámbito equivocado: nombra «{n}», que no es lo pedido" for n in turn.scope_other
                 if len(norm(n)) >= 8 and re.search(rf"(?<!\w){re.escape(norm(n))}(?!\w)", low) and _non_negated(answer, n)]
    crit += [f"acepta una premisa o dato falso: «{s}»" for s in turn.forbid_scores if _score_non_negated(answer, s)]
    crit += [f"acepta una premisa o dato falso: «{p}»" for p in turn.forbid_phrases if _non_negated(answer, p)]
    if turn.forbid_any_score and any(c.startswith("da un marcador") for c in g.claims):
        crit.append("da un marcador donde no hay dato")

    rebuilt = [t for t in turn.forbid_tools if t in tools_used]
    if rebuilt and "abstain" not in g.behavior_detected:
        crit.append("fuente oficial sustituida por una reconstrucción sin decirlo: usó " + ", ".join(rebuilt))

    # --- nombres inventados o de otra entidad (equipos, jugadores, competiciones)
    if turn.behavior != "offtopic" and turn.source != "rules" and not turn.rule_case and not is_refusal(answer):
        invented, g.needs_human_review = unknown_names(turn, answer, payloads, [*names, *players])
        crit += [f"dato inventado: {c}" for c in invented]

    # --- herramientas
    g.tools_missing = [t for t in turn.tools_needed if not any(alt in tools_used for alt in t.split("|"))]
    if turn.tools_allowed is not None:
        g.tools_unexpected = sorted(set(tools_used) - set(turn.tools_allowed))
    if g.tools_missing:
        interp.append("herramientas necesarias no usadas: " + ", ".join(g.tools_missing))
    if turn.behavior == "offtopic" and tools_used:
        interp.append("usó herramientas en una pregunta fuera de alcance")

    # --- conducta
    detected = set(g.behavior_detected)
    on_topic = (not turn.entity_keys) or any(k and k in re.sub(r"[^a-z0-9]+", "", low) for k in turn.entity_keys)
    if turn.behavior == "answer":
        if rule is not None:
            for f in rule.facts or []:
                issue = rule_answer_issue(f, answer) if f.kind == "rule_cited" else None
                if f.kind == "rule_cited" and issue:
                    pres.append(issue)
                elif f.kind != "rule_cited" and not answer_ok(f, answer):
                    interp.append("respuesta incorrecta")
        elif g.facts_required and not g.facts_present:
            if detected & {"offtopic"}:
                interp.append("rechaza la pregunta cuando debía responder")
            elif detected & {"clarify", "abstain"} and not crit:
                interp.append("aclara o se abstiene cuando debía responder")
            elif on_topic:
                pres.append("faltan los hechos requeridos: " + ", ".join(g.facts_missing))
            else:
                interp.append("no responde a lo preguntado (intención mal entendida)")
        elif g.facts_missing:
            pres.append("faltan hechos requeridos: " + ", ".join(g.facts_missing))
    elif turn.behavior == "clarify":
        if "clarify" not in detected:
            interp.append("responde sin aclarar" if "abstain" not in detected else "se abstiene cuando debía aclarar")
        elif turn.clarify_mentions and not any(norm(m) in low or key(m) in low for m in turn.clarify_mentions if m):
            pres.append("aclara sin mencionar las opciones")
    elif turn.behavior == "abstain":
        if rule is not None:
            if not answer_ok(rule.facts[0], answer):  # type: ignore[index]
                interp.append("no reconoce que el reglamento no lo recoge")
        elif "abstain" not in detected:
            interp.append("no reconoce que el dato no consta" if "clarify" not in detected else "aclara cuando debía abstenerse")
        if g.facts_missing and "abstain" in detected:
            pres.append("faltan hechos requeridos: " + ", ".join(g.facts_missing))
    elif turn.behavior == "offtopic" and "offtopic" not in detected:
        interp.append("responde cuando debía rechazar la pregunta")
    if not answer.strip():
        interp.append("respuesta vacía")
    elif not presentation_ok(answer):
        pres.append("presentación: contiene JSON o IDs técnicos, o es demasiado larga")

    if crit:
        g.verdict, g.error_class, g.reasons = "critical", CRITICAL, crit + interp + pres
    elif interp:
        g.verdict, g.error_class, g.reasons = "fail", INTERPRETATION, interp + pres
    elif pres:
        g.verdict, g.error_class, g.reasons = "partial", PRESENTATION, pres
    elif g.needs_human_review:  # nada contradice la respuesta, pero hay algo que no se puede contrastar: nunca «correcto» por defecto
        g.verdict, g.reasons = "review", ["necesita revisión humana: " + "; ".join(g.needs_human_review)]
    return g
