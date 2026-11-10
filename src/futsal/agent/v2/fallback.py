"""v2: respaldo determinista ante una aclaración insistente (tras los 2 empujones el LLM sigue sin llamar a herramientas).

Elige UN plan por reglas (tabla conservadora: si no encaja exactamente no hay respaldo) y compone la respuesta en código SOLO con lo que devolvió la
herramienta (`items`, `count`, `shown`) más TODAS sus `notes`. Puro: la ejecución y la traza las hace el agente.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from futsal.agent.v2.rules import _fold
from futsal.domain.dates import MONTHS, WEEKDAY_NAMES, DateHint

MAX_LINES = 20
CATEGORIES = ("senior", "juvenil", "cadete", "infantil", "alevin", "benjamin")
_SCORER = re.compile(r"\b(maximos? goleadores?|maximo goleador|pichichis?)\b")
_CATEGORY = re.compile(rf"\b({'|'.join(CATEGORIES)})(?:es|s)?\b")
_CLUB = re.compile(r"\btodos (?:los|sus) equipos (?:del|de)\s+(.+?)(?:\s+(?:en|por|de todas|durante)\b.*)?[\s?!.¿]*$")
_OFFICIAL = re.compile(r"\bque partidos (?:ha|han)\s+(?:pitado|arbitrado)\s+(?:(?:el|la)\s+(?:arbitro|arbitra)\s+)?(.+?)"
                       r"(?:\s+(?:esta temporada|este ano|en total|hasta ahora))?[\s?!.¿]*$")


@dataclass(frozen=True)
class Plan:
    plan: str
    tool: str
    args: dict[str, Any]


def _span(question: str, m: re.Match[str]) -> str:
    """Trozo `m[1]` del texto ORIGINAL (el plegado conserva las posiciones)."""
    return question[m.start(1):m.end(1)].strip()


def choose_plan(question: str, hint: DateHint | None) -> Plan | None:
    """El plan que encaja de forma inequívoca; con varios candidatos o ninguno, None."""
    low = _fold(question)
    plans: list[Plan] = []
    if hint is not None:
        args: dict[str, Any] = {"date_from": hint.date_from.isoformat(), "date_to": hint.date_to.isoformat()}
        if hint.time_from and hint.time_to:
            args.update(time_from=f"{hint.time_from:%H:%M}", time_to=f"{hint.time_to:%H:%M}")
        plans.append(Plan("matches_by_date", "matches_by_date", {**args, "limit": 100}))
    cats = set(_CATEGORY.findall(low))
    if _SCORER.search(low) and len(cats) == 1:
        plans.append(Plan("scorers_ranking", "scorers_ranking", {"category": next(iter(cats))}))
    if (m := _CLUB.search(low)) and 2 <= len(name := _span(question, m)) <= 80:
        plans.append(Plan("club_overview", "club_overview", {"name": name, "results": True}))
    if (m := _OFFICIAL.search(low)) and 2 <= len(name := _span(question, m)) <= 80:
        plans.append(Plan("official_matches", "find_official", {"name": name, "include_matches": True}))
    return plans[0] if len(plans) == 1 else None


def _day(hint: DateHint) -> str:
    def one(d: date) -> str:
        month = next(k for k, v in MONTHS.items() if v == d.month and k != "setiembre")
        return f"{WEEKDAY_NAMES[d.weekday()]} {d.day} de {month} de {d.year}"
    return f"el {one(hint.date_from)}" if hint.date_from == hint.date_to else f"entre el {one(hint.date_from)} y el {one(hint.date_to)}"


def _lines(lines: list[str], count: int) -> list[str]:
    out = lines[:MAX_LINES]
    if count > len(out):
        out.append(f"y {count - len(out)} más")
    return out


def _score(s: Any) -> str:
    return f" ({s['home']}-{s['away']})" if isinstance(s, dict) and s.get("home") is not None else ""


def _name(v: Any) -> str:
    return str(v.get("name") if isinstance(v, dict) else v)


def _finish(head: str, lines: list[str], notes: list[Any]) -> str:
    out = [head, *[f"- {x}" for x in lines]]
    if notes:
        out += ["", "Notas:", *[f"- {n}" for n in notes]]
    return "\n".join(out)


def parse(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("items"), list) else None


def club_options(data: dict[str, Any]) -> list[str] | None:
    """Nombres de los clubes candidatos si el resultado es ambiguo (o hay más de uno); None si es un único club claro."""
    clubs: dict[str, bool] = {}
    for it in data["items"]:
        head = it.get("club", it) if isinstance(it, dict) else {}
        key = str(head.get("name"))
        clubs[key] = clubs.get(key, False) or bool(head.get("ambiguous"))
    return list(clubs) if len(clubs) > 1 or any(clubs.values()) else None


def compose(plan: Plan, data: dict[str, Any], hint: DateHint | None) -> tuple[str | None, str]:
    """(respuesta, motivo si no hay): la respuesta usa solo `items`, `count`, `shown` y TODAS las `notes` del resultado."""
    items, count, notes = data["items"], int(data.get("count") or 0), list(data.get("notes") or [])
    if plan.plan == "official_matches" and count == 0:
        sugg = [str(s["name"]) for s in data.get("suggestions") or [] if isinstance(s, dict) and s.get("name")]
        head = f"«{plan.args['name']}» no figura como árbitro en las actas almacenadas."
        if sugg:
            head += f" Nombres parecidos (posibles, sin confirmar que sean la persona pedida): {'; '.join(sugg)}."
        return _finish(head, [], notes), ""
    if count == 0 or not items:
        return None, "count_0"
    if plan.plan == "matches_by_date" and hint is not None:
        band = f" entre las {hint.time_from:%H:%M} y las {hint.time_to:%H:%M}" if hint.time_from and hint.time_to else ""
        lines = [f"{it.get('date')} {it.get('time') or 'hora sin confirmar'} · {_name(it['home'])} - {_name(it['away'])}{_score(it.get('score'))}"
                 f" ({_name(it['competition'])}, {_name(it['group'])})" for it in items]
        return _finish(f"{count} {'partido' if count == 1 else 'partidos'} {_day(hint)}{band}:", _lines(lines, count), notes), ""
    if plan.plan == "scorers_ranking":
        lines = [f"{it['rank']}. {_name(it['player'])} ({_name(it['team'])}, {_name(it['competition'])}, {_name(it['group'])}): {it['goals']} goles" for it in items]
        return _finish(f"Goleadores {plan.args['category']} según las tablas oficiales ({count} en total):", _lines(lines, count), notes), ""
    if plan.plan == "club_overview":
        if club_options(data) is not None:
            return None, "ambiguous"
        lines = []
        for it in items:
            s = it["summary"]
            lines.append(f"{_name(it['team'])} ({_name(it['competition'])}, {_name(it['group'])}): {s['played']} jugados, {s['won']} ganados, {s['drawn']} empatados, "
                         f"{s['lost']} perdidos, {s['goals_for']} goles a favor y {s['goals_against']} en contra")
        return _finish(f"{count} {'equipo' if count == 1 else 'equipos'} de {items[0]['club']['name']}:", _lines(lines, count), notes), ""
    if plan.plan == "official_matches":  # una sola persona con su lista de partidos; varias, solo sus nombres
        if len(items) > 1:
            lines = [f"{it['name']}: {it['matches']} partidos" for it in items]
            return _finish(f"Varios árbitros coinciden con «{plan.args['name']}»:", _lines(lines, count), notes), ""
        it = items[0]
        listed = [f"{m.get('date')} {m.get('time') or 'hora sin confirmar'} · {m['home']} - {m['away']}{_score(m.get('score'))} ({_name(m['competition'])})"
                  for m in it.get("matches_list") or []]
        total = int(it["matches"])
        shown = listed[:MAX_LINES]
        if total > len(shown):
            shown.append(f"y {total - len(shown)} más")
        return _finish(f"{it['name']} figura en {total} {'partido' if total == 1 else 'partidos'}:", shown, notes), ""
    return None, "sin_plan"
