"""Reglas deterministas de AGENT_ARCH=v2 (código, no prompt): alcance amplio, premisas, fechas y sugerencias. Puro: sin E/S."""

import json
import re
import unicodedata
from datetime import time
from typing import Any

from futsal.domain.dates import DateHint, describe

NOTE_HEAD = "\n\n[Nota de la aplicación, no escrita por el usuario]"
SUGGESTION_NOTE = "no figura; ofrécelas como posibles sin atribuirles datos"
SUGGESTION_TOOLS = frozenset({"find_player", "find_official"})
BROAD_ACT_MESSAGE = (
    "Alcance amplio: el usuario pide «todos/todas» o ya respondió a una aclaración; no vuelvas a pedir competición, grupo ni categoría. Usa ya las "
    "herramientas v2 (matches_by_date, club_overview, scorers_ranking, teams_ranking, find_player, find_official, officials_ranking) sin filtro de "
    "grupo y di en la respuesta qué has asumido."
)
BROAD_NOTE = (
    NOTE_HEAD + " Alcance amplio (el usuario pide «todos/todas/cualquier…» o la consulta abarca todas las competiciones: personas, un día, una categoría, un club): no preguntes competición, grupo ni categoría; consulta con las "
    "herramientas v2 y di qué has asumido. Solo pregunta si hay varios clubes o jugadores distintos con el mismo nombre."
)
RANKING_NOTE = (
    NOTE_HEAD + " Ranking de toda una categoría: mezcla grupos y competiciones distintos (con distinto número de partidos), así que dilo "
    "expresamente («son grupos distintos y no del todo comparables») e indica el grupo de cada equipo. Escribe los goles como «X a favor y Y en "
    "contra», nunca como un marcador «X-Y»."
)
_BROAD = re.compile(r"\b(todos|todas|cualquier|cualquiera|toda la temporada)\b")
# Consultas que por naturaleza abarcan todas las competiciones (las herramientas v2 las resuelven sin grupo): preguntar «¿qué competición?» no
# tiene sentido. Personas (árbitros, en qué equipo juega alguien), partidos de un día o de un pabellón, rankings de toda una categoría y clubes.
_MULTI_SCOPE = (
    ("referee", re.compile(r"\barbitr\w*|\b(ha|han|hayan) pitado\b|\bpartidos ha pitado\b")),
    ("player", re.compile(r"\ben que equipo juega\b|\bque jugadores\b|\bjugadores del\b")),
    ("date", re.compile(r"\bpartidos\b.*\b(hay|se juegan|juegan|jugaran)\b.*\b(lunes|martes|miercoles|jueves|viernes|sabado|domingo|fin de semana|hoy|manana)\b"
                        r"|\b(que|cuantos) partidos\b.*\b(\d{1,2} de [a-z]+|fin de semana)\b")),
    ("category_ranking", re.compile(r"\b(maximo goleador|mas goles|menos goles|mas victorias|mas partidos|ha encajado menos|ha marcado mas)\b"
                                    r".*\b(senior|juvenil|juveniles|femenin\w*|categoria)\b"
                                    r"|\b(equipo|equipos) (senior|juvenil|femenino)\b.*\b(mas|menos)\b")),
    ("club", re.compile(r"\bque equipos tiene\b|\bequipos del\b")),
)
# Un nombre propio de varias palabras con mayúscula («Real Sala Aranjuez Norte», «U.D. Pozuelo Dorado»): si el catálogo no lo reconoce, lo
# correcto es buscarlo con las herramientas y decir que no consta, no preguntar la competición.
_PROPER_NAME = re.compile(r"(?<![¿¡.]\s)(?<!^)\b(?:[A-ZÁÉÍÓÚÑ][\w.'’]*\.?\s+){1,}[A-ZÁÉÍÓÚÑ][\w.'’]+")
_PREMISE = re.compile(r"\b(creo que|me han dicho que|me dijeron que|tengo entendido que|he oido que|he leido que|no es (?:verdad|cierto) que|es (?:verdad|cierto) que"
                      r"|dicen que|segun me han dicho)\b")


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def multi_scope_kind(text: str) -> str | None:
    """Tipo de consulta que abarca todas las competiciones por naturaleza (árbitro, jugador, día, ranking de categoría, club) o None."""
    low = _fold(text)
    return next((kind for kind, rx in _MULTI_SCOPE if rx.search(low)), None)


def wants_broad_scope(text: str) -> bool:
    """¿La pregunta (o la respuesta a una aclaración) trae un cuantificador de alcance o es de las que abarcan todas las competiciones?"""
    return _BROAD.search(_fold(text)) is not None or multi_scope_kind(text) is not None


_REFINE = re.compile(r"^\W*(y|e|pero|vale|ok|ahora|solo|y solo|y si|y de|y en|y los|y las|y el|y la)\b")


def refines_multi_scope(text: str, previous: str) -> bool:
    """¿Es un seguimiento corto («¿y solo los de juvenil?», «¿y por la tarde?») de una consulta que abarcaba todas las competiciones?"""
    low = _fold(text).strip()
    return bool(previous) and len(low.split()) <= 8 and _REFINE.search(low) is not None and multi_scope_kind(previous) is not None


def names_something(raw: str) -> bool:
    """¿La pregunta nombra algo propio de varias palabras con mayúscula (fuera del inicio de la frase)?"""
    return _PROPER_NAME.search(raw.strip().lstrip("¿¡")) is not None


def premise_of(text: str) -> str | None:
    """La afirmación del usuario («creo que juega en X») o None."""
    low = _fold(text)
    m = _PREMISE.search(low)
    if m is None:
        return None
    return text[m.start():][:140].strip()


def premise_note(claim: str) -> str:
    return (NOTE_HEAD + f" El usuario afirma algo («{claim}»). No lo des por cierto: compruébalo con las herramientas y di expresamente si la evidencia "
            "lo confirma o no lo confirma (o si no consta).")


DAY_LIMIT = 100  # un día entero de partidos en una sola llamada (tope de la API /v2)


def date_note(hint: DateHint) -> str:
    return (NOTE_HEAD + f" Fecha interpretada: {describe(hint)}. Para matches_by_date usa exactamente estos valores"
            + f" (date_from={hint.date_from}, date_to={hint.date_to}"
            + (f", time_from={hint.time_from:%H:%M}, time_to={hint.time_to:%H:%M}" if hint.time_from and hint.time_to else "") + ")"
            + f" y limit={DAY_LIMIT} en UNA sola llamada (cubre el día entero); no la repitas por categoría ni competición salvo que el usuario lo pida."
            + " Si count supera lo mostrado, da el total y resume.")


def _norm_time(value: Any) -> time | None:
    try:
        return time.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def repair_matches_args(args: dict[str, Any], hint: DateHint) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Argumentos de `matches_by_date` corregidos con la pista de fechas (determinista). Devuelve (argumentos, procedencia o None si no hubo cambio)."""
    fixed = dict(args)
    start, end = hint.date_from.isoformat(), hint.date_to.isoformat()
    if fixed.get("date_from") != start or (fixed.get("date_to") or fixed.get("date_from")) != end:
        fixed["date_from"] = start
        fixed["date_to"] = end
    if hint.time_from and hint.time_to and (_norm_time(fixed.get("time_from")) != hint.time_from or _norm_time(fixed.get("time_to")) != hint.time_to):
        fixed["time_from"], fixed["time_to"] = f"{hint.time_from:%H:%M}", f"{hint.time_to:%H:%M}"
    changed = (fixed.get("date_from") != args.get("date_from") or (fixed.get("date_to") or fixed.get("date_from")) != (args.get("date_to") or args.get("date_from"))
               or fixed.get("time_from") != args.get("time_from") or fixed.get("time_to") != args.get("time_to"))
    if not changed:
        return args, None
    return fixed, {"field": "matches_by_date", "source": "dates", "phrase": hint.phrase, "reason": "arguments_differ_from_date_hint",
                   "value": {k: fixed[k] for k in ("date_from", "date_to", "time_from", "time_to") if k in fixed}}


def add_suggestion_note(text: str) -> str:
    """Resultado de find_player/find_official sin coincidencias pero con sugerencias: añade la nota obligatoria a `notes`."""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if not isinstance(data, dict) or data.get("items") != [] or not data.get("suggestions"):
        return text
    raw = data.get("notes")
    notes: list[Any] = raw if isinstance(raw, list) else []
    if SUGGESTION_NOTE not in notes:
        data["notes"] = [*notes, SUGGESTION_NOTE]
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
