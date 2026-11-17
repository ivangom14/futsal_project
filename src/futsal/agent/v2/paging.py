"""Paginación conversacional de listados v2 (determinista, sin LLM).

Un listado (partidos de un día, goleadores, ranking de equipos, árbitros, partidos de un árbitro) se guarda en el estado de la conversación
SOLO como consulta: herramienta, filtros, posición, tamaño de página y total (`Listing`), nunca sus filas. Los seguimientos
«los siguientes», «10 más», «continúa», «el resto», «los anteriores», «solo los juveniles», «solo los de Preferente», «¿y el domingo?»,
«a partir de las 18» u «ordénalos por hora» se traducen aquí en la misma herramienta con otros argumentos y una página nueva; la respuesta
se compone con las filas que devuelve la API (ni una llamada al LLM). Cualquier otra pregunta sigue el flujo normal.

Garantías: el orden de la API es total (fecha, hora, id / valor, nombre, id), así que con datos estables las páginas no se solapan ni
dejan huecos. Si los datos cambian entre dos páginas (una actualización), una fila puede desplazarse: el total se recalcula en cada
página y se informa tal cual.
"""

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from futsal.agent.v2.rules import _fold
from futsal.domain.dates import MONTHS, WEEKDAY_NAMES, parse_date_hint, parse_hours

PAGE_SIZE = 10
MAX_PAGE = 50  # «muéstrame 30», «el resto»: como mucho 50 filas por página
LIST_TOOLS = frozenset({"matches_by_date", "scorers_ranking", "teams_ranking", "officials_ranking", "find_official"})
PAGE_ARGS = ("offset", "limit")
CATEGORY_WORDS = {"juvenil": "juvenil", "juveniles": "juvenil", "senior": "senior", "seniors": "senior", "cadete": "cadete",
                  "cadetes": "cadete", "infantil": "infantil", "infantiles": "infantil", "alevin": "alevin", "alevines": "alevin",
                  "benjamin": "benjamin", "benjamines": "benjamin", "veteranos": "veteranos"}
GENDER_WORDS = {"femenino": "female", "femenina": "female", "femeninos": "female", "femeninas": "female", "masculino": "male",
                "masculina": "male", "masculinos": "male"}
COMP_GENERIC = frozenset({"fs", "futbol", "sala", "division", "grupo", "fase", "final", "previa", "copa", "liga", "trofeo", "comunidad",
                          "aficionado", "juvenil", "senior", "femenino", "femenina", "primera", "segunda", "autonomica", "honor", "de", "del",
                          "la", "el", "los", "las", "y", "i", "ii", "iii", "eliminatorias"})

_NEXT = re.compile(r"\b(siguientes?|mas\s+resultados|ver\s+mas|muestrame\s+mas|ensename\s+mas|dame\s+mas|pon\s+mas|continua|continuar|"
                   r"sigue|seguir|el\s+resto|los\s+demas|las\s+demas|resto|otros|otras|mas)\b")
_PREV = re.compile(r"\b(anteriores|anterior|atras|volver\s+al?\s+principio|primeros|primeras)\b")
_REST = re.compile(r"\b(el\s+resto|los\s+demas|las\s+demas|todos\s+los\s+demas|resto)\b")
_ORDER = re.compile(r"\b(ordena(?:los|las)?|ordenad(?:os|as)|por\s+hora|por\s+fecha|cronologic)\w*")
_SIZE = re.compile(r"(?<![\d:])(\d{1,3})(?![\d:])")
_LEAD = re.compile(r"^\W*(y|e|ahora|solo|solamente|unicamente|pero|vale|ok|vale\s+y|de|del|los|las|el|la|muestrame|ensename|dame|dime|pon|"
                   r"ver|veamos|siguiente|siguientes|seguir|venga|por\s+favor|"
                   r"mira|filtra|quiero|y\s+solo|y\s+ahora|ahora\s+solo|y\s+de|y\s+del|y\s+el|y\s+la|y\s+los|y\s+las|a\s+partir|desde|entre|"
                   r"antes|hasta|ordena\w*|continua|sigue|siguientes|los\s+siguientes|las\s+siguientes|mas|otros|otras|el\s+resto)\b")
_QUESTION = re.compile(r"\b(quien|quienes|cual|cuales|como|donde|por\s+que|porque|cuando|cuanto|cuantos|cuanta|cuantas)\b")


@dataclass
class Listing:
    """La consulta de un listado mostrado (nunca sus filas)."""
    tool: str
    args: dict[str, Any]  # filtros, sin offset/limit
    offset: int
    limit: int
    count: int
    next_offset: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> "Listing | None":
        if not isinstance(raw, dict) or raw.get("tool") not in LIST_TOOLS or not isinstance(raw.get("args"), dict):
            return None
        try:
            return cls(str(raw["tool"]), dict(raw["args"]), int(raw.get("offset") or 0), int(raw.get("limit") or PAGE_SIZE),
                       int(raw.get("count") or 0), raw.get("next_offset") if isinstance(raw.get("next_offset"), int) else None)
        except (TypeError, ValueError):
            return None


@dataclass
class Followup:
    args: dict[str, Any]
    offset: int
    limit: int
    reason: str  # next | prev | size | rest | filter | order
    changed: list[str] = field(default_factory=list)  # filtros cambiados (para la respuesta y la traza)


def listing_from_call(tool: str, args: dict[str, Any], body: Any) -> Listing | None:
    """El listado que resulta de UNA llamada (con `offset` en la respuesta = paginable); partidos de un árbitro solo con include_matches."""
    if tool not in LIST_TOOLS or not isinstance(body, dict) or "offset" not in body or not isinstance(body.get("items"), list):
        return None
    if tool == "find_official" and not args.get("include_matches"):
        return None
    if body.get("people"):  # varios árbitros con ese nombre: no hay listado que paginar hasta que se elija uno
        return None
    filters = {k: v for k, v in args.items() if k not in PAGE_ARGS and v is not None}
    shown = len(body["items"])
    limit = int(args.get("limit") or 0) or max(shown, PAGE_SIZE)
    nxt = body.get("next_offset")
    return Listing(tool, filters, int(body.get("offset") or 0), limit, int(body.get("count") or 0), nxt if isinstance(nxt, int) else None)


def _competition_word(low: str, competitions: list[str]) -> str | None:
    """«solo los de Preferente» → «preferente» si esa palabra distingue competiciones del catálogo (no genérica ni de categoría)."""
    vocab = {w for c in competitions for w in _fold(c).split() if len(w) >= 4 and w not in COMP_GENERIC}
    hits = [w for w in re.findall(r"[a-z0-9]+", low) if w in vocab]
    return hits[0] if len(hits) == 1 else None


def parse_followup(text: str, listing: Listing, competitions: list[str], clock: Callable[[], datetime]) -> Followup | None:
    """Seguimiento corto de `listing` o None (entonces la pregunta sigue el flujo normal). Solo frases cortas que empiezan como un seguimiento
    («y…», «ahora…», «solo…», «los siguientes»…) y no son una pregunta nueva («¿quién ganó el domingo?»)."""
    low = _fold(text).strip()
    words = low.split()
    if not words or len(words) > 10 or not _LEAD.search(low) or (_QUESTION.search(low) and not _NEXT.search(low)):
        return None
    args = dict(listing.args)
    changed: list[str] = []
    # --- filtros (vuelven a la primera página)
    cats = {CATEGORY_WORDS[w] for w in re.findall(r"[a-z]+", low) if w in CATEGORY_WORDS}
    if len(cats) == 1 and listing.tool in ("matches_by_date", "scorers_ranking", "teams_ranking", "officials_ranking"):
        cat = next(iter(cats))
        if args.get("category") != cat:
            args["category"], changed = cat, [*changed, f"categoría {cat}"]
    genders = {GENDER_WORDS[w] for w in re.findall(r"[a-z]+", low) if w in GENDER_WORDS}
    if len(genders) == 1 and listing.tool in ("matches_by_date", "scorers_ranking", "teams_ranking"):
        g = next(iter(genders))
        if args.get("gender") != g:
            args["gender"], changed = g, [*changed, "femenino" if g == "female" else "masculino"]
    if listing.tool == "matches_by_date":
        word = _competition_word(low, competitions)
        if word and args.get("competition") != word:
            args["competition"], changed = word, [*changed, f"competición «{word}»"]
        hint = parse_date_hint(text, clock)
        current = (args.get("date_from"), args.get("date_to") or args.get("date_from"))
        if hint is not None and (hint.date_from.isoformat(), hint.date_to.isoformat()) != current:
            args["date_from"], args["date_to"] = hint.date_from.isoformat(), hint.date_to.isoformat()
            args.pop("time_from", None)
            args.pop("time_to", None)  # otro día: la franja anterior no se arrastra salvo que se diga otra
            changed.append("fecha")
        start, end = (hint.time_from, hint.time_to) if hint is not None and hint.time_from else parse_hours(text)
        if start or end:
            tf, tt = f"{start:%H:%M}" if start else "00:00", f"{end:%H:%M}" if end else "23:59"
            if (args.get("time_from"), args.get("time_to")) != (tf, tt):
                args["time_from"], args["time_to"] = tf, tt
                changed.append("franja horaria")
    size_m = _SIZE.search(low)
    in_date = parse_hours(text) != (None, None) or parse_date_hint(text, clock) is not None  # «el sábado 7», «a partir de las 18»: no es un tamaño
    size = min(max(int(size_m[1]), 1), MAX_PAGE) if size_m and not in_date else None
    if changed:
        return Followup(args, 0, size or PAGE_SIZE, "filter", changed)
    if _ORDER.search(low):
        return Followup(args, 0, size or listing.limit, "order")
    if _PREV.search(low) and not _NEXT.search(low):
        return Followup(args, max(listing.offset - (size or listing.limit), 0), size or listing.limit, "prev")
    if _REST.search(low) and listing.next_offset is not None:
        return Followup(args, listing.next_offset, min(listing.count - listing.next_offset, MAX_PAGE), "rest")
    if _NEXT.search(low):
        nxt = listing.next_offset if listing.next_offset is not None else listing.count
        return Followup(args, nxt, size or listing.limit, "next")
    if size is not None and len(words) <= 4:  # «muéstrame 20»: la misma consulta desde el principio con otro tamaño
        return Followup(args, 0, size, "size")
    return None


# ----------------------------------------------------------------------------- composición de la página
def _day(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    month = next(k for k, v in MONTHS.items() if v == d.month and k != "setiembre")
    return f"{WEEKDAY_NAMES[d.weekday()]} {d.day} de {month}"


def describe(tool: str, args: dict[str, Any]) -> str:
    """Qué listado es («partidos del sábado 14 de noviembre entre las 15:00 y las 23:59, juvenil»)."""
    extra = [x for x in (args.get("category"), {"female": "femenino", "male": "masculino"}.get(str(args.get("gender"))),
                         f"«{args['competition']}»" if args.get("competition") else None, f"pista «{args['venue']}»" if args.get("venue") else None) if x]
    tail = f" ({', '.join(extra)})" if extra else ""
    if tool == "matches_by_date":
        a, b = args.get("date_from"), args.get("date_to") or args.get("date_from")
        when = f"del {_day(a)}" if a == b else f"entre el {_day(a)} y el {_day(b)}"
        band = f" entre las {args['time_from']} y las {args['time_to']}" if args.get("time_from") and args.get("time_to") else ""
        return f"Partidos {when}{band}{tail}"
    if tool == "scorers_ranking":
        return f"Goleadores según las tablas oficiales{tail}"
    if tool == "teams_ranking":
        metric = {"goals_for": "goles a favor", "goals_against": "menos goles en contra", "wins": "victorias",
                  "points_per_game": "puntos por partido"}.get(str(args.get("metric")), str(args.get("metric")))
        return f"Ranking de equipos por {metric}{tail}"
    if tool == "officials_ranking":
        return f"Árbitros con más partidos{tail}"
    return f"Partidos arbitrados por {args.get('name')}"


def _score(s: Any) -> str:
    if isinstance(s, dict) and s.get("home") is not None:
        return f" {s['home']}-{s['away']}"
    return f" {s}" if isinstance(s, str) and s else ""


def _name(v: Any) -> str:
    return str(v.get("name") if isinstance(v, dict) else v)


def _short_day(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{WEEKDAY_NAMES[d.weekday()][:3]} {d.day:02d}/{d.month:02d}"


def line(tool: str, it: dict[str, Any], one_day: bool = False) -> str:
    """Una fila, compacta y con todo lo necesario (lo mismo que ve el usuario en la tabla). En un listado de UN día la fecha va en la cabecera."""
    if tool in ("matches_by_date", "find_official"):
        when = f"{'' if one_day else _short_day(it.get('date'))} {it.get('time') or '(hora sin confirmar)'}".strip()
        status = {"postponed": " · aplazado", "suspended": " · suspendido", "cancelled": " · cancelado"}.get(str(it.get("status")), "")
        return f"{when} · {_name(it.get('home'))} – {_name(it.get('away'))}{_score(it.get('score'))}{status} · {it.get('comp', '')}".rstrip(" ·")
    if tool == "scorers_ranking":
        return f"{it.get('rank')}. {_name(it.get('player'))} ({_name(it.get('team'))} · {it.get('comp', '')}): {it.get('goals')} goles"
    if tool == "teams_ranking":
        return (f"{it.get('rank')}. {_name(it.get('team'))} ({it.get('comp', '')}): {it.get('goals_for')} a favor, {it.get('goals_against')} en contra, "
                f"{it.get('won')} victorias en {it.get('played')} partidos")
    return f"{it.get('rank')}. {it.get('name')}: {it.get('matches')} partidos"


def page_text(tool: str, args: dict[str, Any], body: dict[str, Any], reason: str, changed: list[str]) -> str:
    items = body.get("items") or []
    count, offset = int(body.get("count") or 0), int(body.get("offset") or 0)
    head = describe(tool, args)
    if not items:
        if count and offset >= count:
            return f"{head}: ya te he mostrado los {count} resultados; no hay más."
        notes = [str(n) for n in body.get("notes") or [] if n][:2]
        return f"{head}: no hay resultados con ese filtro en los datos disponibles." + (f" ({'; '.join(notes)})" if notes else "")
    shown = f"{offset + 1}–{offset + len(items)}" if len(items) > 1 else f"{offset + 1}"
    intro = f"{head}: mostrando {shown} de {count}." if count > len(items) or offset else f"{head}: {count} {'resultado' if count == 1 else 'resultados'}."
    if reason == "order":
        intro += " Ya están ordenados de forma cronológica (o por su valor en los rankings)."
    one_day = tool == "matches_by_date" and (args.get("date_to") or args.get("date_from")) == args.get("date_from")
    out = [intro, *[f"- {line(tool, it, one_day)}" for it in items]]
    nxt = body.get("next_offset")
    if isinstance(nxt, int):
        out.append(f"¿Quieres que te muestre los siguientes {min(PAGE_SIZE, count - nxt)}?")
    elif offset:
        out.append("No hay más resultados.")
    return "\n".join(out)


def page_note(listing: Listing) -> str:
    """Para el LLM, dentro del resultado de la primera página: cómo presentarla (rango, total e invitación) sin pedir más páginas."""
    shown = listing.next_offset - listing.offset if listing.next_offset is not None else listing.count - listing.offset
    if listing.next_offset is None:
        return "Lista completa: son todos los resultados del filtro."
    return (f"Página de {shown} de {listing.count} (count = total exacto). Di «Mostrando {listing.offset + 1}–{listing.offset + shown} de {listing.count}» y "
            f"termina con «¿Quieres que te muestre los siguientes {min(PAGE_SIZE, listing.count - listing.next_offset)}?». No pidas más páginas: la "
            "aplicación las muestra cuando el usuario lo pide. Para una cifra total usa count, nunca las filas.")
