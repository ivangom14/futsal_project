"""Router determinista EN SOMBRA (Fase 0 de la optimización de tokens).

Analiza una pregunta y registra qué ruta HABRÍA elegido (intención, entidades, ámbito, nivel Tier 0/1/2, confianza y motivo).
No ejecuta nada: es una función pura, sin E/S, sin LLM y sin herramientas. El agente la llama, anota el resultado en la traza y sigue
exactamente el mismo flujo de siempre. Un fallo del router nunca puede afectar a la respuesta (el agente lo aísla).

Niveles (solo informativos en esta fase):
- tier0: intención única de alta precisión, ámbito y entidades resolubles → bastaría una llamada al LLM sin esquemas de herramientas.
- tier1: intención reconocida pero con algo abierto (compuesta, acta, agregado, referencia, confianza media…) → herramientas acotadas.
- tier2: no reconocida, ambigua o ajena → flujo completo actual.

Reutiliza `domain.competitions` (pistas y candidatas), `criteria.mentions_rules/mentions_domain/norm` y los alias de `config`.
La resolución de equipos reales (`find_teams`) NO se hace aquí: se marca como `pending` y condiciona la aptitud para Tier 0.
"""

import difflib
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from futsal.agent.criteria import mentions_domain, mentions_rules, norm
from futsal.domain.competitions import (
    Competition,
    Hints,
    candidates,
    extract_hints,
    fold,
    merge_hints,
    scope_resolved,
)

INTENTS = ("standings", "matches", "scorers", "teams_players", "rules", "composite", "ambiguous", "unrecognized")
TIERS = ("tier0", "tier1", "tier2")
CONFIDENCE = ("high", "medium", "low")
TIER0_INTENTS = frozenset({"standings", "scorers", "rules", "matches", "teams_players"})
TIER0_SUBINTENTS = frozenset({"result", "next", "last", "round", "table", "list", "rule"})  # lo que una llamada determinista resuelve

# ---------------------------------------------------------------------------------------------------------------------
# Vocabulario (sobre texto normalizado: minúsculas, sin acentos ni signos). Fuertes = inequívocos; débiles = pueden ser otra cosa.
# ---------------------------------------------------------------------------------------------------------------------
_STRONG: dict[str, re.Pattern[str]] = {
    "standings": re.compile(r"\b(clasificacion|clasificaciones|clasificado|clasificada|clasificados|lider|lideres|lidera|liderato|lideran|posicion"
                            r"|puesto|tabla de posiciones|pole|va (primero|segundo|tercero|cuarto|ultimo)|esta (primero|segundo|ultimo))\b"),
    "scorers": re.compile(r"\b(goleador|goleadores|goleadora|pichichi|pichichis|anotador|anotadores|maximo goleador|mejor goleador"
                          r"|quien (ha )?marcado mas|tabla de goleadores|(jugadores|quienes) (que )?(han )?(marc\w+|anot\w+))\b"),
    "matches": re.compile(r"\b(resultado|resultados|partido|partidos|jornada|jornadas|proximo|siguiente|juega|jugara|jugo|juegan|enfrenta|"
                          r"enfrentara|enfrentan|marcador|calendario|gano|ganado|perdio|perdido|empato|empates|empatado|"
                          r"contra quien|a que hora|cuando juega)\b"),
    "teams_players": re.compile(r"\b(que equipos|equipos (hay|participan|forman|tiene|componen)|lista de equipos|cuantos equipos|plantilla|"
                                r"jugadores (de|del|de la)|en que grupo (esta|juega|participa))\b"),
}
_WEAK: dict[str, re.Pattern[str]] = {
    "standings": re.compile(r"\b(puntos|pts|ascenso|descenso|asciende|desciende|descender|ascender|zona|clasi)\b"),
    "scorers": re.compile(r"\b(goles lleva|ha marcado|han marcado|marco|marcaron|marcar|goles|marcador)\b"),
    "matches": re.compile(r"\b(ultimo|ultima|ultimos|juego|jugados|jugado|disputado|disputados|quedo|quedaron|termino|horario|"
                          r"visitante|local)\b"),
}
_STRONG_SPANS = re.compile(r"\b((primer|segundo|tercer|ultimo|penultimo)s? clasificad\w+|tabla de goleadores|"
                           r"(ultimo|ultima|ultimos|ultimas) (partido|partidos|resultado|resultados|jornada))\b")
_REPORT = re.compile(r"\b(acta|actas|arbitro|arbitros|arbitro|colegiado|tarjeta|tarjetas|amarilla|amarillas|expulsado|expulsados|expulsion|"
                     r"alineacion|alineaciones|suplente|suplentes|titulares|minuto|minutos|asistente|cronometrador|campo|pabellon|direccion|donde juega)\b")
_AGGREGATE = re.compile(r"\b(media de goles|racha|forma|promedio|equipos que (han )?marcado|mas goles en las ultimas|goles en las ultimas|"
                        r"ultimas \w+ jornadas|ultimos \w+ partidos|ultimas \w+ partidos|con mas goles|goles a favor|goles en contra|diferencia de goles)\b")
_COMPARE = re.compile(r"\b(compar\w+|frente a|versus|vs|diferencia entre|mejor que|peor que|contra el segundo|con el segundo)\b")
_RULES_STRONG = re.compile(r"\b(reglamento|reglamentos|reglas de juego|reglas del juego|regla|reglas|circular|circulares|normativa|sancion|"
                           r"sanciones|sancionar|tiempo muerto|tiempos muertos|saque de (banda|esquina|meta|centro)|tiro libre|libre directo|"
                           r"libre indirecto|doble penalti|falta acumulada|faltas acumuladas|cesion|cesiones|barrera|revision de video|var|"
                           r"cesped|medidas de la pista|dimensiones|fuera de juego)\b")
_RULES_PATTERN = re.compile(r"\b(se puede|esta permitido|permitido|prohibido|puede (el|un|una)|pueden|que ocurre si|que pasa si|cuanto dura|"
                            r"cuantos (jugadores|cambios|tiempos|segundos|minutos|metros)|es falta|es penalti|cuando se (pita|concede|"
                            r"sanciona|saca|cobra)|como se (saca|cobra|reanuda)|a las cuantas|que significa|que dice|cual es la regla|"
                            r"que sucede|cuenta como|cuentan como|cuando te (sacan|pitan|echan|expulsan|sancionan)|te (sacan|echan|expulsan)|"
                            r"por (una )?mano|se (considera|pita|castiga|sanciona)|como se juega|es legal|es valido|vale el gol)\b")
_GAME_TERMS = re.compile(r"\b(falta|faltas|tarjeta roja|tarjeta amarilla|roja|amarilla|penalti|penal|portero|guardameta|balon|pista|cesion|"
                         r"barrera|banda|esquina|corner|saque|mano|manos|sustitucion|sustituciones|cambios|mitad|mitades|duracion|lesion|"
                         r"cronometro|doble|protestar|protestas|banquillo|cesped|equipacion|dorsal|video|tiro libre|descuento|prorroga)\b")
_OFFTOPIC = re.compile(r"\b(chiste|olvida|ignora|instrucciones|prompt|receta|tiempo hace|clima|bitcoin|politica|presidente del gobierno|"
                       r"traspaso|fichaje|fichajes|apuestas|cuotas)\b")
_REFS = re.compile(r"\b(ese equipo|este equipo|ese jugador|este jugador|esa jornada|ese grupo|el otro grupo|ese partido|este partido|"
                   r"su (ultimo|proximo|siguiente|anterior|primer) partido|sus (partidos|goles|resultados|jugadores)|su (posicion|puesto|"
                   r"clasificacion)|el anterior|le (ha|han|toca|queda)|ellos|el otro|lo mismo)\b|^y\b")
_VAGUE = re.compile(r"\b(como (ha |han )?(quedado|quedo|va|van|le va|esta|anda)|que tal (va|le va|esta)|como le ha ido)\b")
_ROUND = re.compile(r"\bjornada (\d{1,2})\b|\bj(\d{1,2})\b")
_N_LAST = re.compile(r"\b(ultimos|ultimas) (\d+|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\b")

# Léxico para tolerar erratas (≥ 7 letras; no se corrige lo corto para no inventar palabras)
_LEXICON = ("clasificacion", "clasificaciones", "clasificado", "goleador", "goleadores", "resultado", "resultados", "partido", "partidos",
            "jornada", "jornadas", "reglamento", "arbitro", "arbitros", "tarjeta", "tarjetas", "proximo", "siguiente", "posicion",
            "equipos", "jugadores", "alineacion", "alineaciones", "puntos", "lidera", "liderato", "normativa", "circular", "sancion")

_STOP_CAPS = frozenset(fold(w) for w in (
    "que quien quienes cual cuales cuanto cuantos cuanta cuantas como cuando donde por para con sin sobre segun en el la los las un una "
    "de del al y o e es son hay ha han fue era dime dame muestrame necesito quiero quisiera puedes podrias "
    "grupo grupos jornada jornadas liga copa torneo trofeo rffm rfef fifa fs senior juvenil temporada futsito si no hola gracias").split())
_CONNECT = frozenset({"de", "del", "la", "las", "los", "el", "y", "e", "-", "&", "san", "santa"})
_TOKEN = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9][\wÁÉÍÓÚÜÑáéíóúüñ.'’&\-]*")


@dataclass(frozen=True)
class RouterDecision:
    intent: str
    tier: str
    confidence: str
    score: float
    reason: str
    tier0_eligible: bool
    needs_full: bool  # habría que recurrir al sistema completo actual (Tier 2)
    subintent: str | None = None
    intents: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()
    entities: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["intents"], data["signals"] = list(self.intents), list(self.signals)
        return data


def resolve_scope(competitions: Sequence[Competition], aliases: dict[str, Any], prev_questions: Sequence[str],
                  question: str) -> tuple[Hints, bool, int]:
    """Pistas de ámbito (mensaje actual + preguntas previas del USUARIO; lo nuevo manda), si bastan para fijar competición y grupo,
    y nº de competiciones candidatas. Misma lógica que `Agent._resolve_scope`, sin tocar el agente."""
    if not competitions:
        return Hints(), False, 0
    merged = Hints()
    for q in prev_questions:
        merged = merge_hints(merged, extract_hints(q, aliases, list(competitions)), aliases)
    hints = merge_hints(merged, extract_hints(question, aliases, list(competitions)), aliases)
    cands = candidates(list(competitions), hints, aliases)
    return hints, scope_resolved(cands, hints), len(cands)


def _fuzzy(text: str) -> tuple[str, list[str]]:
    """Corrige erratas de palabras clave (cambio mínimo, ≥ 7 letras). Devuelve el texto y las correcciones aplicadas."""
    fixed, changes = [], []
    for w in text.split():
        if len(w) >= 7 and w not in _LEXICON and not w.isdigit():
            hit = difflib.get_close_matches(w, _LEXICON, n=1, cutoff=0.86)
            if hit:
                changes.append(f"{w}->{hit[0]}")
                w = hit[0]
        fixed.append(w)
    return " ".join(fixed), changes


def named_entities(question: str, competitions: Sequence[Competition], hints: Hints) -> list[str]:
    """Nombres propios (equipos o jugadores) citados: secuencias con mayúscula inicial o en mayúsculas que no son palabras de
    pregunta ni de competición/grupo. Es heurístico: no resuelve ni valida nada (eso es `find_teams`)."""
    comp_words: set[str] = set()
    for c in competitions:
        comp_words.update(fold(c.name).split())
        for _, g in c.groups:
            comp_words.update(fold(g).split())
    comp_words.update(w for p in (hints.alias or "").split() for w in fold(p).split())
    out: list[str] = []
    current: list[str] = []
    pending: list[str] = []  # conectores entre dos palabras con mayúscula

    def flush() -> None:
        nonlocal current, pending
        if current:
            out.append(" ".join(current))
        current, pending = [], []

    for index, m in enumerate(_TOKEN.finditer(question)):
        tok = m.group(0).rstrip(",;:")
        if tok.endswith(".") and "." not in tok[:-1]:  # punto de frase, no de abreviatura («F.S.», «C.D.» lo conservan)
            tok = tok[:-1]
        low = fold(tok)
        looks_name = (tok[:1].isupper() or tok.isupper()) and not tok.isdigit()
        if looks_name and low and low not in _STOP_CAPS and not all(p in comp_words for p in low.split()) and (index > 0 or tok.isupper()):
            current += pending + [tok]
            pending = []
        elif current and low in _CONNECT:
            pending.append(tok)
        else:
            flush()
    flush()
    for q in re.findall(r"[«\"“']([^»\"”']{3,40})[»\"”']", question):
        if q not in out:
            out.append(q)
    unique: list[str] = []
    for name in out:
        if name not in unique and fold(name) not in _STOP_CAPS:
            unique.append(name)
    return unique[:4]


def route(question: str, *, competitions: Sequence[Competition] = (), hints: Hints | None = None, scope_ok: bool = False,
          groups_total: int = 0, focus: dict[str, Any] | None = None, has_history: bool = False) -> RouterDecision:
    """Decisión en sombra para UNA pregunta. `focus` es el foco verificado de la conversación (nombres, nunca IDs)."""
    hints = hints or Hints()
    focus = focus or {}
    text = norm(question)
    text, typos = _fuzzy(text)
    signals: list[str] = []
    ents: dict[str, Any] = {"names": named_entities(question, competitions, hints), "rounds": [], "refs": [], "typos": typos,
                            "scope": {"filters": hints.describe(), "resolved": scope_ok, "groups_in_catalog": groups_total}}
    rounds = [int(a or b) for a, b in _ROUND.findall(text)]
    ents["rounds"] = rounds
    refs = sorted({m.group(0) for m in _REFS.finditer(text)})
    if ents["names"]:  # «¿en qué campo juega el DOSA su próximo partido?»: el sujeto está en la propia frase
        refs = [r for r in refs if r.startswith(("ese ", "este ", "esa ", "el otro", "y"))]
    ents["refs"] = refs
    ents["focus_team"] = focus.get("team")

    def decision(intent: str, tier: str, conf: str, score: float, reason: str, *, sub: str | None = None,
                 intents: tuple[str, ...] = ()) -> RouterDecision:
        eligible = tier == "tier0"
        ents.setdefault("entity_resolution", "not_needed" if intent == "rules" or not ents["names"] else "pending_find_teams")  # informativo
        return RouterDecision(intent, tier, conf, round(score, 2), reason, eligible, tier == "tier2", sub, intents or (intent,),
                              tuple(signals), ents)

    if _OFFTOPIC.search(text) and not mentions_rules(question):
        signals.append("offtopic_term")
        return decision("unrecognized", "tier2", "high", 0.9, "offtopic_or_instruction_override")

    # --- puntuación por intención: fuerte = 2, débil = 1 (una errata corregida cuenta como débil) ---
    score: dict[str, float] = {}
    for intent, pat in _STRONG.items():
        hits = pat.findall(text)
        if hits:
            score[intent] = 2.0 + (0.5 if len(set(map(str, hits))) > 1 else 0.0)
            signals.append(f"strong:{intent}")
    residue = _STRONG_SPANS.sub(" ", text)  # las señales débiles se buscan fuera de lo ya reconocido como fuerte
    for intent, pat in _WEAK.items():
        if intent not in score and pat.search(residue):
            score[intent] = 1.0
            signals.append(f"weak:{intent}")
    report = bool(_REPORT.search(text))
    aggregate = bool(_AGGREGATE.search(text))
    compare = bool(_COMPARE.search(text))
    if report and "matches" not in score:
        score["matches"] = 2.0
        signals.append("report_terms")
    rules_strong = bool(_RULES_STRONG.search(text)) or mentions_rules(question)
    rules_generic = bool(_RULES_PATTERN.search(text)) and bool(_GAME_TERMS.search(text))
    data_specific = bool(ents["names"] or rounds or refs or score.get("standings") or score.get("scorers")
                         or re.search(r"\b(resultado|resultados|clasificacion|grupo|jornada|proximo|ultimo|ultima)\b", text))
    if rules_strong or (rules_generic and not data_specific):
        score["rules"] = 2.5 if rules_strong else 2.0
        signals.append("strong:rules" if rules_strong else "generic:rules")
        report = False  # «tarjeta roja por una mano» en una pregunta de reglas no es un acta
        if not data_specific:  # «cuánto dura cada mitad del partido»: «partido» no es aquí un dato de competición
            for k in ("matches", "standings", "scorers"):
                if k in score and k not in ("standings", "scorers") or score.get(k, 9) < 2.0:
                    score.pop(k, None)
    if typos:
        signals.append("typo_corrected")

    if _VAGUE.search(text) and not any(score.get(i, 0) >= 2.0 for i in ("standings", "scorers", "matches", "rules")) and not report:
        return decision("composite", "tier1", "medium", 0.6, "vague_status_request:standings+matches", sub="snapshot",
                        intents=("standings", "matches"))
    if not score:
        if refs and (has_history or focus):
            return decision("ambiguous", "tier2", "low", 0.2, "followup_without_intent")
        if mentions_domain(question):
            return decision("ambiguous", "tier2", "low", 0.3, "domain_word_without_clear_intent")
        return decision("unrecognized", "tier2", "high" if not ents["names"] else "low", 0.8 if not ents["names"] else 0.3,
                        "no_domain_vocabulary")

    if "matches" in score and not report and any(score.get(i, 0) >= 2.0 for i in ("standings", "scorers", "rules")):
        if set(_STRONG["matches"].findall(text)) <= {"jornada", "jornadas", "partido", "partidos"}:
            score.pop("matches")  # «clasificación tras la jornada 1», «goleadores de la jornada 4»: filtros, no otra consulta
            signals.append("round_is_filter")
    present = tuple(i for i in ("standings", "matches", "scorers", "teams_players", "rules") if score.get(i, 0) >= 2.0)
    weak_only = tuple(i for i in score if i not in present)
    # el solapamiento «máximo goleador … en qué puesto» es compuesto; «posición del equipo» no
    if len(present) >= 2 or (compare and present) or (aggregate and present) or (len(present) == 1 and weak_only and compare):
        return decision("composite", "tier1", "high" if len(present) >= 2 else "medium", 0.7,
                        f"composite:{'+'.join(present) or 'compare'}{'+compare' if compare else ''}{'+aggregate' if aggregate else ''}",
                        intents=present)
    if not present:  # solo señales débiles
        intent = max(score, key=lambda k: score[k])
        return decision(intent, "tier1", "medium" if not typos else "low", 0.5, f"weak_signal_only:{intent}", intents=(intent,))

    intent = present[0]
    sub = _subintent(intent, text, report, aggregate, rounds)
    conf, sc = ("high", 0.9) if not typos or all(len(c.split("->")[0]) >= 9 for c in typos) else ("medium", 0.65)
    if weak_only:
        conf, sc = "medium", min(sc, 0.7)
        signals.append("competing_weak:" + "+".join(weak_only))
    named = bool(ents["names"]) or bool(focus.get("team"))

    # --- condiciones de ámbito y entidades ---
    if intent != "rules" and groups_total > 1 and not scope_ok and not ents["names"] and not refs:
        return decision("ambiguous", "tier2", "medium", 0.4, f"scope_missing:{intent}", sub=sub)
    if refs:
        if focus or has_history:
            return decision(intent, "tier1", "medium", 0.5, "reference_needs_conversation_focus", sub=sub)
        return decision("ambiguous", "tier2", "low", 0.2, "unresolved_reference", sub=sub)
    if intent in ("matches", "teams_players") and sub in ("result", "next", "last") and not named and not scope_ok and groups_total > 1:
        return decision(intent, "tier1", "medium", 0.5, "team_or_scope_missing", sub=sub)

    t0 = (intent in TIER0_INTENTS and sub in TIER0_SUBINTENTS and conf == "high" and not report and not aggregate
          and not (intent == "matches" and sub in ("result", "next", "last") and not named))
    if t0 and intent in ("standings", "scorers") and sub == "table" and groups_total > 1 and not scope_ok and not ents["names"]:
        t0 = False
    resolution = "not_needed" if intent in ("rules",) or not ents["names"] else "pending_find_teams"
    ents["entity_resolution"] = resolution
    if t0:
        return decision(intent, "tier0", conf, sc, f"single_intent:{intent}/{sub};entities={resolution};scope={'ok' if scope_ok or groups_total <= 1 else 'n/a'}", sub=sub)
    why = "report_detail" if report else "aggregate" if aggregate else f"sub={sub};conf={conf}"
    return decision(intent, "tier1", conf if conf != "high" else "medium", min(sc, 0.7), f"not_tier0:{why}", sub=sub)


def _subintent(intent: str, text: str, report: bool, aggregate: bool, rounds: list[int]) -> str:
    if intent == "rules":
        return "rule"
    if report:
        return "report"
    if aggregate:
        return "aggregate"
    if intent == "standings":
        return "table"
    if intent == "scorers":
        return "table" if re.search(r"\b(grupo|liga|categoria|competicion|equipo|maximo|mejor|top|pichichi|quien|quienes|tabla)\b", text) else "player"
    if intent == "teams_players":
        return "list" if re.search(r"\b(que equipos|equipos|lista|cuantos equipos)\b", text) else "player_lookup"
    if _N_LAST.search(text):
        return "last_n"
    if rounds:
        return "round"
    if re.search(r"\b(proximo|siguiente|juega|jugara|cuando juega|a que hora|contra quien)\b", text):
        return "next"
    if re.search(r"\b(ultimo|ultima|ultimos)\b", text):
        return "last"
    if re.search(r"\b(cuantos|cuantas|total|contar)\b", text):
        return "count"
    return "result"
