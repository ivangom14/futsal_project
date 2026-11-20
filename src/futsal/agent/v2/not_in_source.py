"""v2: preguntas cuyo dato principal el acta de la RFFM no recoge (sustituciones, resultado por periodos, minuto añadido, marcador tras cada gol).

Puro y determinista. NO incluye penaltis (ambiguo con goles de penalti, que pueden constar como evento).
Si la pregunta pide además algo resoluble («¿quién marcó y en qué minuto hubo cambios?») no se responde de forma determinista: solo se añade una nota.
"""

import re
from dataclasses import dataclass

from futsal.agent.messages import NOT_IN_REPORT
from futsal.agent.v2.rules import NOTE_HEAD, _fold

_RULES = re.compile(r"\b(se pueden|se puede|puede(?:n)?|permite(?:n)?|permitido|reglamento|reglamentario|norma|normativa|regla|reglas|maximo|limite|circular)\b")
_SUBS = (
    re.compile(r"\bminutos?\b.*\b(cambios?|sustituci\w+|entr\w+|sali\w+|suplentes?)\b"),
    re.compile(r"\b(cambios?|sustituci\w+|suplentes?)\b.*\bminutos?\b"),
    re.compile(r"\b(entraron|entro|salieron|salio)\b.*\b(suplentes?|banquillo)\b"),
    re.compile(r"\b(suplentes?)\b.*\b(entraron|entro|salieron|salio)\b"),
    re.compile(r"\b(sustituciones|cambios|suplentes)\b.*\b(del|en el|de este|de ese|de la)\s+(partido|encuentro|acta)\b"),
    re.compile(r"\b(partido|encuentro|acta)\b.*\b(sustituciones|cambios|suplentes)\b"),
)
_PERIOD = (
    re.compile(r"\b(resultado|marcador)\b.*\b(descanso|medio tiempo|primer tiempo|segundo tiempo|primera parte|segunda parte|por periodos|por partes|por tiempos)\b"),
    re.compile(r"\b(descanso|medio tiempo)\b.*\b(iba|iban|ganaba|perdia|resultado|marcador)\b"),
    re.compile(r"\b(iba|iban|ganaba|perdia)\b.*\b(descanso|medio tiempo)\b"),
    re.compile(r"\bresultado por (periodos|partes|tiempos)\b"),
)
_ADDED = (re.compile(r"\b(minutos?|tiempo) (anadidos?|de descuento|extra)\b"), re.compile(r"\b(descuento|anadido)\b.*\b(minutos?|tiempo)\b"))
_AFTER_GOAL = (re.compile(r"\b(marcador|resultado)\b.*\b(tras|despues de|en)\s+(cada|todos los)\s+(gol|goles|evento)\b"),
               re.compile(r"\b(tras|despues de)\s+cada\s+gol\b.*\b(marcador|resultado)\b"))
_KINDS: tuple[tuple[str, tuple[re.Pattern[str], ...], str], ...] = (
    ("substitutions", _SUBS, "las sustituciones (quién entra o sale ni en qué minuto)"),
    ("period_score", _PERIOD, "el resultado por periodos ni el del descanso"),
    ("added_time", _ADDED, "el minuto o tiempo añadido"),
    ("score_after_goal", _AFTER_GOAL, "el marcador tras cada gol"),
)
_OTHER = re.compile(r"\b(quien(?:es)? (?:marco|marcaron|metio|anoto|gano|pito|arbitro)|goleadores?|cuantos goles|tarjetas?|amonest\w+|expuls\w+|arbitros?|alineacion\w*"
                    r"|clasificacion|resultado final|jugadores?)\b")


@dataclass(frozen=True)
class NotInReport:
    kind: str
    what: str
    alone: bool  # True: no hay nada más resoluble en la pregunta → respuesta determinista
    in_source: bool = False  # el acta SÍ lo trae pero aún no se incorpora (fútbol 11: sustituciones): nunca decir que no consta


# Lo que el acta del deporte SÍ recoge aunque Futsito aún no lo incorpore (vacío desde la Fase 3C) y lo que ya está disponible:
# entonces la pregunta sigue el flujo normal (herramientas), sin respuesta determinista de «no consta».
IN_SOURCE_NOT_INGESTED: dict[str, frozenset[str]] = {}
AVAILABLE = {"football11": frozenset({"substitutions"})}  # Fase 3C: sustituciones de fútbol 11 interpretadas


def detect(question: str, sport: str | None = None) -> NotInReport | None:
    low = _fold(question)
    if _RULES.search(low):
        return None  # pregunta de reglamento («¿cuántos cambios se pueden hacer?»): flujo normal
    for kind, patterns, what in _KINDS:
        if kind in AVAILABLE.get(sport or "", ()) and any(p.search(low) for p in patterns):
            return None  # dato disponible en este deporte: lo responden las herramientas
        if any(p.search(low) for p in patterns):
            return NotInReport(kind, what, _OTHER.search(low) is None, kind in IN_SOURCE_NOT_INGESTED.get(sport or "", ()))
    return None


NOT_INGESTED = ("El acta de la RFFM de este partido incluye {qué}, pero todavía no las he incorporado a mis datos, así que no puedo "
                "detallarlas.")


def answer(found: NotInReport) -> str:
    if found.in_source:
        return NOT_INGESTED.format(qué=found.what.split(" (")[0])
    return NOT_IN_REPORT.format(qué=found.what)


def note(found: NotInReport) -> str:
    if found.in_source:
        return (NOTE_HEAD + f" La pregunta incluye un dato que el acta trae pero que aún no está incorporado ({found.what}). Para esa parte "
                f"responde exactamente «{answer(found)}» y contesta el resto con las herramientas; no inventes ni deduzcas ese dato.")
    return (NOTE_HEAD + f" La pregunta incluye un dato que el acta de la RFFM no recoge ({found.what}). Para esa parte responde exactamente «{answer(found)}» "
            "y contesta el resto con las herramientas; no inventes ni deduzcas ese dato.")
