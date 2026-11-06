"""Presentación de una respuesta (Futsito, Fase 25): foco de la conversación, tipo de respuesta, fuentes con frescura y correspondencia,
visuales estructurados y preguntas sugeridas. Puro: parte de lo que las tools devolvieron realmente; nunca interpreta el texto libre
del modelo para decidir qué dibujar ni inventa datos, fechas o fuentes."""

import json
import re
from datetime import UTC, datetime
from typing import Any

from futsal.agent.criteria import norm, states_tool_limit
from futsal.agent.trace import RunRecord, ToolCallRecord
from futsal.domain.cards import KINDS, summarize
from futsal.domain.competitions import fold

DATASET_OF_TOOL = {"get_standings": "standings", "get_scorer_table": "scorers", "list_matches": "matches", "get_match": "matches",
                   "get_match_report": "reports", "list_match_events": "reports", "top_scorers": "reports"}
DATASET_TITLE = {"matches": "Partidos y resultados", "standings": "Clasificación oficial", "scorers": "Goleadores oficiales",
                 "reports": "Actas de partidos"}
ANALYSIS_QUESTION = ("analiza", "analisis", "recomienda", "recomendacion", "tactica", "estrategia", "opinas", "crees", "valora",
                     "pronostico", "favorito", "deberia", "puntos fuertes", "puntos debiles", "como juega")
ANALYSIS_ANSWER = ("en mi opinion", "recomiendo", "probablemente", "podria ser", "parece que", "mi analisis", "diria que")
GENERIC_TEAM_WORDS = frozenset({"cd", "c", "d", "club", "cde", "f", "s", "fs", "futbol", "sala", "a", "b", "de", "del", "la", "el", "e", "u"})
MAX_ROWS = 20
MAX_MATCHES = 12
MAX_EVENTS = 20
PARTICLES = frozenset({"de", "del", "la", "las", "los", "y", "da", "do"})
MAX_BARS = 10


def _json(text: str | None) -> Any:
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def executed(rec: RunRecord) -> list[ToolCallRecord]:
    """Llamadas que se ejecutaron de verdad y no fallaron (las rechazadas por el guardarraíl o con error no son evidencia)."""
    return [t for t in rec.tool_calls if not t.is_error and not t.refused]


# --- Foco de la conversación (Bloque 2) ---------------------------------------------------------------------------------------
def compute_focus(rec: RunRecord, names: dict[str, dict[int, str]], groups_used: dict[int, dict[str, str]],
                  official: list[dict[str, Any]], question: str) -> dict[str, str]:
    """De qué trató ESTA consulta, con nombres tomados de las tools: equipo, competición/grupo, jugador, jornada."""
    focus: dict[str, str] = {}
    for call in executed(rec):
        args = call.arguments
        if isinstance(args.get("team_id"), int) and (n := names["teams"].get(args["team_id"])):
            focus["team"] = n
        if isinstance(args.get("player_id"), int) and (n := names["players"].get(args["player_id"])):
            focus["player"] = n
        if isinstance(args.get("round_id"), int) and (n := names["rounds"].get(args["round_id"])):
            focus["round"] = n
        if isinstance(args.get("group_id"), int) and (g := groups_used.get(args["group_id"])):
            focus["competition"], focus["group"] = g["competition"], g["group"]
    t0 = rec.tier0 if rec.tier0 and rec.tier0.get("answered") else None
    if t0 and isinstance(t0.get("team_id"), int) and (n := names["teams"].get(t0["team_id"])):
        focus["team"] = n  # Tier 0: la herramienta de clasificación no lleva team_id, pero el equipo consultado es el foco del seguimiento
    if re.search(r"\b(maximo goleador|pichichi|mejor goleador)\b", norm(question)):
        for data in official:
            top = [r for r in data.get("scorers") or [] if isinstance(r, dict) and r.get("rank") == 1]
            if len(top) == 1 and top[0].get("player"):
                focus["player"], focus["team"] = str(top[0]["player"]), str(top[0].get("team") or "") or focus.get("team", "")
    return {k: v for k, v in focus.items() if v}


def merge_focus(prev: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    """Lo nuevo manda; si cambia la competición/grupo sin nombrar equipo, o el equipo sin nombrar jugador, se descarta lo que ya no encaja."""
    out = dict(prev)
    if (new.get("group") or new.get("competition")) and not new.get("team") and (
            new.get("group") != prev.get("group") or new.get("competition") != prev.get("competition")):
        for k in ("team", "player", "round"):
            out.pop(k, None)
    if new.get("team") and new["team"] != prev.get("team") and not new.get("player"):
        out.pop("player", None)
    out.update(new)
    return out


def sanitize_focus(raw: Any) -> dict[str, str]:
    """Foco enviado por el navegador (no fiable): solo claves conocidas y textos cortos."""
    if not isinstance(raw, dict):
        return {}
    return {k: str(raw[k]).strip()[:80] for k in ("team", "player", "competition", "group", "round") if isinstance(raw.get(k), str) and raw[k].strip()}


# --- Tipo de respuesta -----------------------------------------------------------------------------------------------------------
def answer_kind(question: str, answer: str, rec: RunRecord) -> tuple[str, bool]:
    """(`official` | `documental` | `mixed` | `none`, ¿es análisis o recomendación del modelo?)."""
    calls = executed(rec)
    data = any(t.name in DATASET_OF_TOOL for t in calls)
    rag = any(t.name == "search_rules" and (_json(t.result) or {}).get("hits") for t in calls)
    kind = "mixed" if data and rag else "official" if data else "documental" if rag else "none"
    q, a = norm(question), norm(answer)
    analysis = kind != "none" and (any(w in q for w in ANALYSIS_QUESTION) or any(w in a for w in ANALYSIS_ANSWER))
    return kind, analysis


# --- Fuentes -------------------------------------------------------------------------------------------------------------------------
def _cited(answer: str, hit: dict[str, Any]) -> bool:
    """¿La respuesta se apoya en este fragmento? Menciona su regla o página, o copia al menos 4 palabras seguidas de su texto."""
    a = norm(answer)
    rule, pages = hit.get("rule"), str(hit.get("pages") or "")
    if rule and re.search(rf"\bregla (n[ºo.]* ?)?{rule}\b", a):
        return True
    if pages and any(re.search(rf"\b(p|pag|pagina|paginas)\.? ?{re.escape(p)}\b", a) for p in re.split(r"[-–]", pages) if p):
        return True
    words = norm(str(hit.get("text") or "")).split()
    shingles = {" ".join(words[i:i + 4]) for i in range(len(words) - 3)}
    return any(s in a for s in shingles)


def _stamp(captured: str | None, fresh: dict[str, Any] | None) -> dict[str, Any]:
    """Fecha de actualización de unos datos: la captura de la tabla o la frescura de la Fase 21; si no consta, se dice."""
    valid = captured or (fresh or {}).get("last_valid_at")
    if not valid:
        return {"updated_at": None, "freshness": "unknown",
                "updated_note": "No se conoce la fecha de actualización de estos datos."}
    status = (fresh or {}).get("status")
    note = "Datos posiblemente desactualizados." if status == "stale" else None
    return {"updated_at": valid, "freshness": status if status in ("fresh", "stale") else "known", "updated_note": note}


def build_sources(question: str, answer: str, rec: RunRecord, official: list[dict[str, Any]], groups_used: dict[int, dict[str, str]],
                  freshness: dict[int, dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    queried = now.isoformat()
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    captured = {(d.get("group_id"), "standings" if d.get("standings") else "scorers"): (d.get("source") or {}).get("captured_at")
                for d in official if isinstance(d, dict)}
    for call in executed(rec):
        ds, gid = DATASET_OF_TOOL.get(call.name), call.arguments.get("group_id")
        if ds and isinstance(gid, int) and (ds, gid) not in seen:
            seen.add((ds, gid))
            g = groups_used.get(gid, {})
            stamp = _stamp(captured.get((gid, ds)), (freshness.get(gid) or {}).get("datasets", {}).get(ds))
            out.append({"kind": "official_data", "title": f"{DATASET_TITLE[ds]} · RFFM", "queried_at": queried, "cited": None,
                        "detail": " · ".join(x for x in (g.get("competition"), g.get("group")) if x) or None,
                        "pages": None, "text": None, "meta": {}, **stamp})
    reports_seen: set[tuple[Any, ...] | None] = set()
    for call in executed(rec):  # actas de un partido: no llevan group_id, la cabecera da el partido
        data = _json(call.result) if call.name in ("get_match_report", "get_match") else None
        head = (data or {}).get("match") if isinstance(data, dict) else None
        rkey = (head.get("home_team"), head.get("round_number")) if isinstance(head, dict) else None
        if isinstance(head, dict) and head.get("home_team") and rkey not in reports_seen:
            reports_seen.add(rkey)
            out.append({"kind": "official_data", "title": "Acta del partido · RFFM", "queried_at": queried, "cited": None,
                        "detail": f"{head['home_team']} - {head.get('away_team')}" + (f" · Jornada {head['round_number']}" if head.get("round_number") else ""),
                        "pages": None, "text": None, "meta": {}, **_stamp(None, None)})
    hits_seen: set[tuple[Any, ...]] = set()
    for call in executed(rec):
        data = _json(call.result) if call.name == "search_rules" else None
        for hit in (data or {}).get("hits", []) if isinstance(data, dict) else []:
            if not isinstance(hit, dict):
                continue
            key = (hit.get("doc_key"), hit.get("rule"), hit.get("section"), hit.get("pages"), str(hit.get("text"))[:80])
            if key in hits_seen:
                continue
            hits_seen.add(key)
            detail = " · ".join(str(x) for x in (f"Regla {hit['rule']}" if hit.get("rule") else None, hit.get("rule_title"),
                                                 hit.get("section"), hit.get("part")) if x)
            out.append({"kind": "document", "title": str(hit.get("document") or "Documento"), "detail": detail or None,
                        "pages": hit.get("pages"), "text": str(hit.get("text"))[:1200] if hit.get("text") else None,
                        "cited": _cited(answer, hit), "queried_at": queried, "updated_at": None, "freshness": None, "updated_note": None,
                        "meta": {k: hit[k] for k in ("version", "type", "doc_key") if hit.get(k)}})
    return out


# --- Visuales (Bloque 5) ----------------------------------------------------------------------------------------------------------------
def _mentioned(question: str, rows: list[dict[str, Any]], key: str = "team") -> list[dict[str, Any]]:
    q = set(fold(question).split())
    found = []
    for r in rows:
        toks = [t for t in fold(str(r.get(key, ""))).split() if t not in GENERIC_TEAM_WORDS]
        if toks and all(t in q for t in toks):
            found.append(r)
    return found


LINEUP_NOTE = ("El acta indica quiénes son titulares y suplentes, pero no la posición de cada jugador en la pista: "
               "la colocación en el campo es orientativa.")


def _pretty(name: str) -> str:
    """«APARICIO JIMENEZ, JAVIER» -> «Javier Aparicio Jimenez»."""
    if "," in name:
        surname, _, given = name.partition(",")
        name = f"{given.strip()} {surname.strip()}"
    words = name.split()
    return " ".join(w.lower() if i and w.lower() in PARTICLES else w.capitalize() for i, w in enumerate(words))


def _player(p: dict[str, Any]) -> dict[str, Any]:
    """Jugador para la pista: nombre completo y etiqueta corta («J. Aparicio») a partir de «APELLIDOS, NOMBRE»."""
    raw = str(p.get("name") or "")
    surnames, _, given = raw.partition(",")
    first_given, first_surname = (given.split() or [""])[0].capitalize(), (surnames.split() or [""])[0].capitalize()
    short = f"{first_given[0]}. {first_surname}" if first_given and first_surname else _pretty(raw)
    return {"name": _pretty(raw), "short": short, "shirt": p.get("shirt") if isinstance(p.get("shirt"), int) else None,
            "captain": bool(p.get("captain")), "goalkeeper": bool(p.get("goalkeeper"))}


def lineup_from_report(data: dict[str, Any], question: str, focus: dict[str, str]) -> list[dict[str, Any]]:
    """Quinteto inicial y suplentes de UN acta (`get_match_report` con la sección `players`). Sin datos, sin visual."""
    if not data.get("report_available") or not isinstance(data.get("players"), list):
        return []
    head = data.get("match") or {}
    by_team: dict[str, list[dict[str, Any]]] = {}
    for p in data["players"]:
        if isinstance(p, dict) and p.get("team") and p.get("name"):
            by_team.setdefault(str(p["team"]), []).append(p)
    wanted = [t for t in by_team if _mentioned(question, [{"team": t}])] or [t for t in by_team if t == focus.get("team")] or list(by_team)
    out: list[dict[str, Any]] = []
    for team in wanted:
        rows = by_team[team]
        order = lambda p: (not p.get("goalkeeper"), p.get("shirt") if isinstance(p.get("shirt"), int) else 999)  # noqa: E731
        starters = [_player(p) for p in sorted((r for r in rows if r.get("lineup") == "starter"), key=order)]
        if not starters:
            continue
        subs = [_player(p) for p in sorted((r for r in rows if r.get("lineup") == "substitute"), key=order)]
        unknown = [_player(p) for p in sorted((r for r in rows if r.get("lineup") not in ("starter", "substitute")), key=order)]
        out.append({"type": "lineup", "title": f"Alineación · {team}", "subtitle": _match_line(head), "team": team,
                    "court": len(starters) == 5 and sum(1 for s in starters if s["goalkeeper"]) == 1,
                    "starters": starters, "substitutes": subs, "unknown": unknown, "note": LINEUP_NOTE})
    return out


def _match_line(head: dict[str, Any]) -> str:
    score = (f" {head.get('home_score')}-{head.get('away_score')} "
             if head.get("status") == "finished" and isinstance(head.get("home_score"), int) else " - ")
    return f"{head.get('home_team')}{score}{head.get('away_team')}" + (f" · Jornada {head['round_number']}" if head.get("round_number") else "")


def officials_from_report(data: dict[str, Any]) -> dict[str, Any] | None:
    """Árbitros y mesa del acta, tal como figuran (nombre y cargo). `kind` solo elige el dibujo: árbitro, asistente o mesa."""
    people = []
    for o in data.get("officials") or []:
        if isinstance(o, dict) and o.get("name"):
            role = str(o.get("role") or "")
            low = fold(role)
            kind = "assistant" if "asistente" in low else "table" if any(w in low for w in ("cronometr", "anotador", "mesa", "delegado")) else "referee"
            people.append({"name": _pretty(str(o["name"])), "role": role or "Árbitro", "kind": kind})
    if not people or not data.get("report_available"):
        return None
    return {"type": "officials", "title": "Árbitros", "subtitle": _match_line(data.get("match") or {}), "people": people}


def goals_timeline(data: dict[str, Any]) -> dict[str, Any] | None:
    """Goles del acta por minuto. Las tarjetas NO se dibujan: el significado de sus códigos no está verificado."""
    head = data.get("match") or {}
    goals = [g for g in data.get("goals") or [] if isinstance(g, dict) and g.get("type") in ("goal", "own_goal") and g.get("player")]
    if not data.get("report_available") or not goals or not head.get("home_team"):
        return None
    events = []
    for g in goals[:MAX_EVENTS]:
        own = g["type"] == "own_goal"
        scorer_home = g.get("team") == head["home_team"]
        side = ("away" if scorer_home else "home") if own else ("home" if scorer_home else "away")  # el gol propio cuenta para el rival
        events.append({"minute": g.get("minute") if isinstance(g.get("minute"), int) else None, "player": _pretty(str(g["player"])),
                       "side": side, "own_goal": own})
    return {"type": "timeline", "title": "Goles del partido", "subtitle": _match_line(head), "home": head["home_team"], "away": head.get("away_team"),
            "events": events, "partial": len(goals) > MAX_EVENTS}


def _card_events(cards: list[Any], home: Any = None, away: Any = None) -> list[dict[str, Any]]:
    out = []
    for c in cards:
        if isinstance(c, dict) and c.get("card_kind") in KINDS and c.get("player"):
            team = c.get("team")
            out.append({"minute": c.get("minute") if isinstance(c.get("minute"), int) else None, "player": _pretty(str(c["player"])), "team": team,
                        "side": "home" if home and team == home else "away" if away and team == away else None, "kind": c["card_kind"],
                        "round": c.get("round_number") if isinstance(c.get("round_number"), int) else None})
    return sorted(out, key=lambda e: (e["round"] or 0, e["minute"] is None, e["minute"] or 0))


def cards_from_report(data: dict[str, Any]) -> dict[str, Any] | None:
    """Tarjetas de UN acta (sección `cards`), con el significado confirmado de los códigos (`card_kind`). Sin tarjetas, sin visual."""
    head = data.get("match") or {}
    events = _card_events(data.get("cards") or [], head.get("home_team"), head.get("away_team"))
    if not data.get("report_available") or not events:
        return None
    return {"type": "cards", "title": "Tarjetas del partido", "subtitle": _match_line(head), "home": head.get("home_team"), "away": head.get("away_team"),
            "summary": summarize([e["kind"] for e in events]), "events": events, "partial": False}


def cards_from_events(data: Any) -> dict[str, Any] | None:
    """Tarjetas de varios partidos (`list_match_events` con `event_type=card`); `partial` si la herramienta recortó la lista."""
    if not isinstance(data, dict):
        return None
    events = _card_events([e for e in data.get("events") or [] if isinstance(e, dict) and e.get("type") == "card"])
    if not events:
        return None
    raw_total = data.get("total")
    total = raw_total if isinstance(raw_total, int) else len(events)
    return {"type": "cards", "title": "Tarjetas", "subtitle": "Según las actas importadas", "home": None, "away": None,
            "summary": summarize([e["kind"] for e in events]), "events": events[:MAX_EVENTS], "partial": total > min(len(events), MAX_EVENTS)}


def matches_rows(payload: Any, limit: int = MAX_MATCHES) -> tuple[list[dict[str, Any]], int]:
    """Filas de partidos de un `list_matches` y el total que informa la herramienta."""
    matches = [m for m in (payload or {}).get("matches", []) if isinstance(m, dict) and m.get("home_team")] if isinstance(payload, dict) else []
    count = payload.get("count") if isinstance(payload, dict) else None
    total = count if isinstance(count, int) else len(matches)
    rows = [{"round": m.get("round_number"), "date": m.get("date"), "home": m.get("home_team"), "away": m.get("away_team"),
             "score": f"{m['home_score']}-{m['away_score']}" if isinstance(m.get("home_score"), int) and isinstance(m.get("away_score"), int) else None,
             "status": m.get("status"), "past_due": bool(m.get("past_due"))} for m in matches[:limit]]
    return rows, max(total, len(rows))


def _matches_title(call: ToolCallRecord, names: dict[str, dict[int, str]], groups_used: dict[int, dict[str, str]]) -> tuple[str, str | None]:
    a = call.arguments
    gid = a.get("group_id")
    g = groups_used.get(gid) if isinstance(gid, int) else None
    where = " · ".join(x for x in (g or {}).values() if x)
    kind = {"finished": "Resultados", "scheduled": "Próximos partidos"}.get(str(a.get("status")), "Partidos")
    extra = [names["teams"][a["team_id"]] if isinstance(a.get("team_id"), int) and a["team_id"] in names["teams"] else None,
             f"Jornada {names['rounds'][a['round_id']]}" if isinstance(a.get("round_id"), int) and a["round_id"] in names["rounds"] else None]
    return f"{kind}" + (f" · {where}" if where else ""), " · ".join(x for x in extra if x) or None


def visual_note(tool: str, args: dict[str, Any], data: Any, question: str, focus: dict[str, str]) -> str | None:
    """Si la aplicación va a dibujar estos datos, se lo dice al LLM (dentro del resultado de la tool) para que no los repita en texto.
    Solo se avisa cuando el visual SÍ se va a generar (mismas funciones que lo construyen): nunca se pierde información."""
    if not isinstance(data, dict):
        return None
    parts: list[str] = []
    if tool == "get_match_report":
        if officials_from_report(data):
            parts.append("los árbitros")
        if lineup_from_report(data, question, focus):
            parts.append("las alineaciones (titulares y suplentes)")
        if goals_timeline(data):
            parts.append("los goles por minuto")
        if cards_from_report(data):
            parts.append("las tarjetas")
    elif tool == "list_match_events":
        if cards_from_events(data):
            parts.append("las tarjetas")
    elif tool == "list_matches":
        rows, _ = matches_rows(data)
        if len(rows) >= 2 and not (isinstance(args.get("team_id"), int) and args.get("status") == "finished"):
            parts.append("los partidos en una tabla")
    elif tool in ("get_standings", "get_scorer_table") and data.get("status") in ("ok", "incomplete"):
        if len(data.get("standings" if tool == "get_standings" else "scorers") or []) >= 2:
            parts.append("la clasificación" if tool == "get_standings" else "el ranking de goleadores")
    if not parts:
        return None
    return (f"La aplicación ya muestra {', '.join(parts)} como gráfico junto a tu respuesta: no lo repitas en texto (ni en lista ni en tabla). "
            "Responde con una o dos frases que aporten lo esencial (p. ej. el resultado o el líder) y lo demás que haya pedido el usuario.")


def lineup_visuals(question: str, rec: RunRecord, focus: dict[str, str]) -> list[dict[str, Any]]:
    """Visuales de actas: alineaciones, árbitros y goles, con lo que devolvió `get_match_report`. Sin datos, sin visual."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for call in executed(rec):
        data = _json(call.result) if call.name == "get_match_report" else None
        if not isinstance(data, dict):
            continue
        head = data.get("match") or {}
        key = (head.get("home_team"), head.get("away_team"), head.get("round_number"))
        if key in seen:
            continue
        seen.add(key)
        lineups = lineup_from_report(data, question, focus)
        officials, timeline, cards = officials_from_report(data), goals_timeline(data), cards_from_report(data)
        out += lineups[:2] + [v for v in (officials, timeline, cards) if v]
    for call in executed(rec):  # tarjetas de varios partidos
        events_visual = cards_from_events(_json(call.result)) if call.name == "list_match_events" else None
        if events_visual:
            out.append(events_visual)
            break
    return out


def build_visuals(question: str, rec: RunRecord, official: list[dict[str, Any]], names: dict[str, dict[int, str]],
                  focus: dict[str, str] | None = None, groups_used: dict[int, dict[str, str]] | None = None) -> list[dict[str, Any]]:
    visuals: list[dict[str, Any]] = lineup_visuals(question, rec, focus or {})
    for data in official:
        where = " · ".join(x for x in (data.get("competition"), data.get("group")) if x)
        src = data.get("source") or {}
        partial = data.get("status") == "incomplete"
        rows = [r for r in data.get("standings") or [] if isinstance(r, dict)]
        if rows:
            marked = {id(r) for r in _mentioned(question, rows)}
            visuals.append({
                "type": "table", "title": f"Clasificación · {where}", "partial": partial,
                "subtitle": f"Jornada {src['round']}" if src.get("round") else None,
                "columns": [{"key": k, "label": lbl, "align": "left" if k == "team" else "right"} for k, lbl in (
                    ("position", "Pos"), ("team", "Equipo"), ("played", "PJ"), ("won", "G"), ("drawn", "E"), ("lost", "P"),
                    ("goals_for", "GF"), ("goals_against", "GC"), ("goal_difference", "DG"), ("points", "Pts"))],
                "rows": [{**{k: r.get(k) for k in ("position", "team", "played", "won", "drawn", "lost", "goals_for", "goals_against",
                                                   "goal_difference", "points")}, "highlight": id(r) in marked} for r in rows[:MAX_ROWS]]})
            pair = _mentioned(question, rows)
            if len(pair) == 2:
                metrics = (("points", "Puntos"), ("played", "Partidos"), ("goals_for", "Goles a favor"), ("goals_against", "Goles en contra"),
                           ("goal_difference", "Diferencia de goles"))
                visuals.append({"type": "comparison", "title": f"{pair[0]['team']} vs {pair[1]['team']}", "subtitle": where,
                                "series": [str(pair[0]["team"]), str(pair[1]["team"])],
                                "rows": [{"label": lbl, "values": [pair[0].get(k), pair[1].get(k)]} for k, lbl in metrics
                                         if isinstance(pair[0].get(k), int) and isinstance(pair[1].get(k), int)]})
        scorers = [r for r in data.get("scorers") or [] if isinstance(r, dict) and isinstance(r.get("goals"), int)]
        if scorers:
            visuals.append({"type": "bars", "title": f"Goleadores · {where}", "partial": partial,
                            "subtitle": f"Tabla actual · captura {str(src.get('captured_at'))[:10]}" if src.get("captured_at") else None,
                            "rows": [{"label": str(r.get("player")), "sublabel": r.get("team"), "value": r["goals"],
                                      "rank": r.get("rank")} for r in scorers[:MAX_BARS]]})
    form_done = False
    for call in executed(rec):  # partidos listados: tarjetas de resultados / próximos partidos (la forma de un equipo va aparte)
        payload = _json(call.result) if call.name == "list_matches" else None
        if payload is None or (isinstance(call.arguments.get("team_id"), int) and call.arguments.get("status") == "finished"):
            continue
        rows, total = matches_rows(payload)
        if len(rows) >= 2:
            title, subtitle = _matches_title(call, names, groups_used or {})
            visuals.append({"type": "matches", "title": title, "subtitle": subtitle, "rows": rows, "total": total, "partial": total > len(rows)})
            break
    for call in executed(rec):  # forma reciente de un equipo: list_matches(team_id, status=finished)
        if form_done or call.arguments.get("status") != "finished":
            continue
        tid = call.arguments.get("team_id")
        payload = _json(call.result) if call.name == "list_matches" else None
        team = names["teams"].get(tid) if isinstance(tid, int) else None
        matches = [m for m in payload.get("matches", []) if isinstance(m, dict)] if isinstance(payload, dict) else []
        done = [m for m in matches if m.get("status") == "finished" and isinstance(m.get("home_score"), int)
                and isinstance(m.get("away_score"), int)]
        if team and len(done) >= 2:
            visuals.append(_form(team, done))
            form_done = True
    return visuals


def _form(team: str, matches: list[dict[str, Any]]) -> dict[str, Any]:
    rows, points = [], 0
    for m in sorted(matches, key=lambda m: (str(m.get("date") or ""), m.get("round_number") or 0)):
        home = fold(str(m.get("home_team"))) == fold(team)
        gf, ga = (m["home_score"], m["away_score"]) if home else (m["away_score"], m["home_score"])
        res = "V" if gf > ga else "E" if gf == ga else "D"
        points += 3 if res == "V" else 1 if res == "E" else 0
        rows.append({"round": m.get("round_number"), "rival": m.get("away_team") if home else m.get("home_team"),
                     "score": f"{m['home_score']}-{m['away_score']}", "result": res, "points": points, "home": home})
    return {"type": "form", "title": f"Últimos resultados · {team}", "subtitle": "Puntos acumulados en los partidos mostrados", "rows": rows}


# --- Preguntas sugeridas (Bloque 4) ----------------------------------------------------------------------------------------------------------
def build_suggestions(question: str, answer: str, rec: RunRecord, focus: dict[str, str], asked: list[str],
                      sources: list[dict[str, Any]], visuals: list[dict[str, Any]] | None = None) -> list[str]:
    """2-3 preguntas que las tools SÍ pueden responder, a partir de la consulta realizada y el foco; sin llamada extra al LLM."""
    calls = executed(rec)
    if not calls or states_tool_limit(answer) or rec.error or rec.guardrail not in (None, "aclaracion_ambito"):
        return []
    team, player, group = focus.get("team"), focus.get("player"), focus.get("group")
    names_used = {t.name for t in calls}
    scheduled = any(t.arguments.get("status") == "scheduled" for t in calls if t.name == "list_matches")
    finished = any(t.arguments.get("status") == "finished" for t in calls if t.name == "list_matches")
    cands: list[str] = []
    who = team or "este grupo"
    if names_used & {"get_standings"}:
        cands += [f"¿Quiénes son los próximos rivales de {team}?" if team else "¿Cuáles son los próximos partidos del grupo?",
                  f"¿Cómo han quedado los últimos partidos de {team}?" if team else "¿Qué resultados hubo en la última jornada?",
                  f"¿Quién es el máximo goleador de {team}?" if team else "¿Quién es el máximo goleador del grupo?"]
    elif names_used & {"get_scorer_table", "top_scorers"}:
        cands += [f"¿En qué equipo juega {player}?" if player else "¿Cómo va la clasificación?",
                  f"¿Cómo va {team} en la clasificación?" if team else "¿Cómo va la clasificación?",
                  f"¿Cuál es el próximo partido de {team}?" if team else "¿Qué resultados hubo en la última jornada?"]
    elif "get_match_report" in names_used:
        shown = {v["type"] for v in visuals or []}  # lo que ya se ve en gráficos no se vuelve a sugerir
        cands += [q for q, t in (("¿Quién marcó los goles de ese partido?", "timeline"), ("¿Qué tarjetas hubo en ese partido?", "cards"),
                                 ("¿Quiénes fueron los árbitros de ese partido?", "officials")) if t not in shown]
        cands += [f"¿Cómo va {team} en la clasificación?" if team else "¿Cómo va la clasificación del grupo?",
                  f"¿Cuál es el próximo partido de {team}?" if team else "¿Cuáles son los próximos partidos del grupo?"]
    elif "list_matches" in names_used or "get_match" in names_used:
        cands += [f"¿Cómo va {team} en la clasificación?" if team else "¿Cómo va la clasificación?",
                  f"¿Cuál es el próximo partido de {team}?" if team and finished else (f"¿Cómo ha quedado el último partido de {team}?" if team
                                                                                   else "¿Qué resultados hubo en la última jornada?"),
                  f"¿Quién es el máximo goleador de {team}?" if team else "¿Quién es el máximo goleador del grupo?"]
        if scheduled and team:
            cands = [f"¿Cómo han quedado los últimos partidos de {team}?", *cands]
    elif "search_rules" in names_used:
        rules = sorted({str(s.get("detail", "")).split(" · ")[0] for s in sources if s["kind"] == "document" and s.get("cited")
                        and str(s.get("detail", "")).startswith("Regla")})
        cands += [f"Resúmeme la {r} del reglamento" for r in rules[:1]] + ["¿Qué circulares hay sobre este tema?"]
    elif team or group:
        cands += [f"¿Cómo va {who} en la clasificación?", f"¿Cuál es el próximo partido de {team}?" if team else "¿Cuáles son los próximos partidos?"]
    seen = {fold(q) for q in asked} | {fold(question)}
    out: list[str] = []
    for c in cands:
        if fold(c) not in seen and fold(c) not in {fold(x) for x in out}:
            out.append(c)
    return out[:3] if len(out) >= 2 else []
