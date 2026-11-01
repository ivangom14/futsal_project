"""Presentación de una respuesta (Futsito, Fase 25): foco de la conversación, tipo de respuesta, fuentes con frescura y correspondencia,
visuales estructurados y preguntas sugeridas. Puro: parte de lo que las tools devolvieron realmente; nunca interpreta el texto libre
del modelo para decidir qué dibujar ni inventa datos, fechas o fuentes."""

import json
import re
from datetime import UTC, datetime
from typing import Any

from futsal.agent.criteria import norm, states_tool_limit
from futsal.agent.trace import RunRecord, ToolCallRecord
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


def build_visuals(question: str, rec: RunRecord, official: list[dict[str, Any]], names: dict[str, dict[int, str]]) -> list[dict[str, Any]]:
    visuals: list[dict[str, Any]] = []
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
    for call in executed(rec):  # forma reciente de un equipo: list_matches con team_id
        tid = call.arguments.get("team_id")
        payload = _json(call.result) if call.name == "list_matches" else None
        team = names["teams"].get(tid) if isinstance(tid, int) else None
        matches = [m for m in payload.get("matches", []) if isinstance(m, dict)] if isinstance(payload, dict) else []
        done = [m for m in matches if m.get("status") == "finished" and isinstance(m.get("home_score"), int)
                and isinstance(m.get("away_score"), int)]
        if team and len(done) >= 2:
            visuals.append(_form(team, done))
            break
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
                      sources: list[dict[str, Any]]) -> list[str]:
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
