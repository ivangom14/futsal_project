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
    NOTE_HEAD + " Alcance amplio pedido por el usuario («todos/todas/cualquier…»): no preguntes competición, grupo ni categoría; consulta con las "
    "herramientas v2 y di qué has asumido. Solo pregunta si hay varios clubes o jugadores distintos con el mismo nombre."
)
_BROAD = re.compile(r"\b(todos|todas|cualquier|cualquiera|toda la temporada)\b")
_PREMISE = re.compile(r"\b(creo que|me han dicho que|me dijeron que|tengo entendido que|he oido que|he leido que|no es (?:verdad|cierto) que|es (?:verdad|cierto) que"
                      r"|dicen que|segun me han dicho)\b")


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def wants_broad_scope(text: str) -> bool:
    """¿La pregunta (o la respuesta a una aclaración) trae un cuantificador de alcance?"""
    return _BROAD.search(_fold(text)) is not None


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


def date_note(hint: DateHint) -> str:
    return (NOTE_HEAD + f" Fecha interpretada: {describe(hint)}. Para matches_by_date usa exactamente estos valores"
            + f" (date_from={hint.date_from}, date_to={hint.date_to}"
            + (f", time_from={hint.time_from:%H:%M}, time_to={hint.time_to:%H:%M}" if hint.time_from and hint.time_to else "") + ").")


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
