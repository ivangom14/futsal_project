"""Evaluación de COMPRENSIÓN conversacional (sin red, sin LLM real, sin PostgreSQL).

Qué mide: no si el agente «contesta algo», sino QUÉ consultó. Cada turno del conjunto (`understanding_cases.CASES`) declara lo esperado (intención,
competiciones, grupos, equipo, acción) y se compara con las herramientas MCP realmente ejecutadas y sus argumentos. Consultar una competición
distinta de la esperada es un fallo CRÍTICO, aunque la respuesta parezca correcta.

Piezas simuladas (todas deterministas e idénticas para los tres modos `legacy` / `deterministic` / `llm`):
- `World`: catálogo real de 14 competiciones (temporada 22) con equipos, clasificaciones, goleadores y partidos de prueba, servido por un MCP falso.
- `SimLLM`: «modelo obediente»: sigue las notas verificadas de la aplicación y, sin ellas, solo usa palabras clave evidentes (como el rechazo real de
  «¿Cómo va la liga preferente?»). Con `refuse_first` rechaza su primera respuesta (rechazo incorrecto) para probar la recuperación. Su comportamiento
  real con Gemini NO se conoce hasta la validación en vivo; esto mide la aplicación (resolución, notas, guardarraíles), no el modelo.
- `SimInterpreter`: sustituto del intérprete LLM, más tolerante que el resolutor (erratas, ordinales sobre opciones ofrecidas, correferencias).
Tokens: estimados (caracteres/4). Latencia: no medible aquí; se informa el número de llamadas.

CLI: `python -m futsal.agent.understanding_eval --modes legacy,deterministic,llm --out results.json`
"""

import argparse
import asyncio
import difflib
import hashlib
import inspect
import json
import os
import re
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from futsal.agent.agent import OFF_TOPIC_MESSAGE, Agent
from futsal.agent.criteria import norm
from futsal.agent.llm import LLMResponse, Message, ToolCall, Usage
from futsal.agent.memory import Turn, select_turns
from futsal.agent.presentation import compute_focus, merge_focus

MODES = ("legacy", "deterministic", "llm")
INTERPRETER_PREFIX = "INTERPRETE_RFFM"  # primera palabra del prompt de sistema del intérprete (la comparte `interpreter.py`)
MEMORY_GUARDRAILS = (None, "aclaracion_ambito")  # igual que `web.MEMORY_GUARDRAILS`

# --------------------------------------------------------------------------------------------------------------------
# Mundo simulado
# --------------------------------------------------------------------------------------------------------------------
TER, CS1F, CS1P = "TERCERA DIVISIÓN FS", "COPA SENIOR I - fase final- FS", "COPA SENIOR I - fase previa- FS"
PAA, PREF_A, PRI_A = "PRIMERA DIVISION AUTONOMICA AFICIONADO FS", "PREFERENTE AFICIONADO FS", "PRIMERA AFICIONADO FS"
CS2F, CS2P = "COPA SENIOR II - fase final- FS", "COPA SENIOR II - fase previa- FS"
TROF, PAJ, PREF_J = "TROFEO COMUNIDAD DIVISION HONOR JUVENIL FS", "PRIMERA DIVISION AUTONOMICA JUVENIL FS", "PREFERENTE JUVENIL FS"
CJF, CJP, PRI_J = "COPA JUVENIL - fase final- FS", "COPA JUVENIL - fase previa- FS", "PRIMERA JUVENIL FS"
REAL: dict[str, list[str]] = {
    TER: ["Grupo 3", "Grupo 4"], CS1F: ["Eliminatorias"], CS1P: ["Eliminatorias"], PAA: ["Grupo 1", "Grupo 2"], PREF_A: ["Grupo 1", "Grupo 2"],
    CS2F: ["Eliminatorias"], CS2P: ["Eliminatorias"], PRI_A: ["Grupo 1", "Grupo 2", "Grupo 3", "Grupo 4"], TROF: ["Eliminatorias"],
    PAJ: ["Grupo Unico"], PREF_J: ["Grupo 1", "Grupo 2"], CJF: ["Eliminatorias"], CJP: ["Eliminatorias"],
    PRI_J: ["Grupo 1", "Grupo 2", "Grupo 3", "Grupo 4", "Grupo 5"]}
CID = {n: i + 1 for i, n in enumerate(REAL)}
GID = {(n, g): 100 * (i + 1) + j for i, (n, gs) in enumerate(REAL.items()) for j, g in enumerate(gs, 1)}
GROUP_OF = {v: k for k, v in GID.items()}
NAMED_TEAMS: dict[str, list[tuple[str, str]]] = {  # equipo -> (competición, grupo) donde juega
    "C.D. DOSA": [(PAA, "Grupo 2"), (CS1P, "Eliminatorias"), (PAJ, "Grupo Unico"), (PREF_J, "Grupo 1"), (CJF, "Eliminatorias"), (PRI_J, "Grupo 3")],
    "C.D. VALDEMORO": [(PRI_A, "Grupo 2")], "A.D. ALCOBENDAS": [(PAA, "Grupo 1")], "ALCOBENDAS SALA": [(PREF_A, "Grupo 2")],
    "ATLÉTICO PINTO": [(PREF_J, "Grupo 1")], "PINTO C.F.S.": [(PRI_J, "Grupo 3")], "C.D. LEGANÉS": [(TER, "Grupo 3")],
    "ESCUELA LEGANÉS": [(PRI_J, "Grupo 1")], "GETAFE SALA": [(PREF_J, "Grupo 2")], "C.D. TORREJÓN": [(TER, "Grupo 4")],
    "MÓSTOLES CITY": [(PRI_A, "Grupo 4")], "ALCORCÓN SALA": [(PRI_J, "Grupo 5")]}
TEAM_ID = {n: 1000 + i for i, n in enumerate(NAMED_TEAMS)}


def _teams_in(gid: int) -> list[tuple[int, str]]:
    comp, group = GROUP_OF[gid]
    named = [(TEAM_ID[n], n) for n, where in NAMED_TEAMS.items() if (comp, group) in where]
    # el mismo club puede figurar en varios grupos: id distinto por grupo (como en la base real)
    named = [(TEAM_ID[n] * 1000 + gid if len([1 for w in NAMED_TEAMS[n]]) > 1 else TEAM_ID[n], n) for _, n in named]
    return named + [(gid * 10 + k, f"EQUIPO {gid}{'ABCD'[k]}") for k in range(4)]


class FakeMcp:
    """MCP de solo lectura sobre `World`; registra las llamadas (nombre, argumentos)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> list[dict[str, Any]]:
        names = ("list_competitions", "find_teams", "list_teams", "list_rounds", "list_matches", "get_standings", "get_scorer_table", "search_rules")
        return [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}} for n in names]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        a = arguments
        if name == "list_competitions":
            return False, _j({"competitions": [{"id": CID[n], "name": n, "groups": [{"id": GID[(n, g)], "name": g} for g in gs]} for n, gs in REAL.items()],
                              "count": len(REAL)})
        self.calls.append((name, dict(a)))
        if name == "find_teams":
            key = norm(str(a.get("name", "")))
            rows = [{"id": tid, "name": n, "group_id": GID[w], "group": w[1], "competition_id": CID[w[0]], "competition": w[0]}
                    for n, wheres in NAMED_TEAMS.items() if key and key in norm(n) for w in wheres for tid in [_team_id(n, w)]]
            return False, _j({"teams": rows, "count": len(rows)})
        gid = int(a.get("group_id") or 0)
        if name in ("list_teams", "list_rounds", "get_standings", "get_scorer_table", "list_matches") and gid not in GROUP_OF:
            return True, "error_not_found: grupo desconocido"
        if name == "list_teams":
            return False, _j({"teams": [{"id": i, "name": n} for i, n in _teams_in(gid)]})
        if name == "list_rounds":
            return False, _j({"rounds": [{"id": gid * 100 + r, "number": r} for r in range(1, 4)]})
        comp, group = GROUP_OF.get(gid, ("", ""))
        if name == "get_standings":
            rows = [{"position": k + 1, "team_id": i, "team": n, "played": 5, "won": 5 - k, "drawn": 0, "lost": k, "goals_for": 20 - k, "goals_against": 5 + k,
                     "goal_difference": 15 - 2 * k, "points": 15 - 3 * k} for k, (i, n) in enumerate(_teams_in(gid))]
            return False, _j({"status": "ok", "competition": comp, "group": group, "group_id": gid, "source": {"round": 5, "captured_at": "2026-11-01T09:00:00+00:00"},
                              "standings": rows})
        if name == "get_scorer_table":
            tm = _teams_in(gid)
            return False, _j({"status": "ok", "competition": comp, "group": group, "group_id": gid, "source": {"captured_at": "2026-11-01T09:00:00+00:00"},
                              "scorers": [{"rank": k + 1, "player": f"Goleador {gid}-{k + 1}", "player_id": gid * 10 + k, "team": tm[k][1], "goals": 12 - k}
                                          for k in range(3)]})
        if name == "list_matches":
            tm = _teams_in(gid)
            items = []
            for k, (tid, tn) in enumerate(tm):
                away = tm[(k + 1) % len(tm)]
                items.append({"id": gid * 100 + k, "round_number": 1, "home_team": tn, "away_team": away[1], "home_team_id": tid, "away_team_id": away[0],
                              "date": "2026-10-03", "status": "finished", "home_score": 3, "away_score": 1})
                items.append({"id": gid * 100 + 50 + k, "round_number": 6, "home_team": away[1], "away_team": tn, "home_team_id": away[0], "away_team_id": tid,
                              "date": "2026-11-08T10:00:00+00:00", "status": "scheduled"})
            if a.get("team_id"):
                items = [m for m in items if a["team_id"] in (m["home_team_id"], m["away_team_id"])]
            if a.get("status"):
                items = [m for m in items if m["status"] == a["status"]]
            if a.get("limit"):
                items = items[: int(a["limit"])]
            return False, _j({"matches": items, "count": len(items)})
        if name == "search_rules":
            return False, _j({"hits": [{"document": "Reglamento de juego", "rule": "Regla 7", "page": 12, "text": "Cada mitad dura 20 minutos."}]})
        return True, "error_validacion: herramienta no simulada"


def _team_id(name: str, where: tuple[str, str]) -> int:
    gid = GID[where]
    return TEAM_ID[name] * 1000 + gid if len(NAMED_TEAMS[name]) > 1 else TEAM_ID[name]


def _j(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


# --------------------------------------------------------------------------------------------------------------------
# Modelo simulado
# --------------------------------------------------------------------------------------------------------------------
_NAIVE = (("rules", ("reglamento", "regla ", "reglas", "cuanto dura", "duracion de")), ("scorers", ("goleador", "goles", "pichichi", "marcado")),
          ("next_match", ("proximo", "siguiente partido", "cuando juega")), ("last_result", ("resultado", "ultimo partido", "como quedo")),
          ("teams_list", ("que equipos", "equipos hay", "lista de equipos")), ("standings", ("clasificacion", "puntos", "posicion", "puesto", "lider", "tabla")),
          ("matches", ("partidos", "jornada")))
_IDS = re.compile(r"group_id=(\d+)")
_QUOTED = re.compile(r"«([^»]+)»")


def _naive_intent(question: str) -> str | None:
    low = f" {norm(question)} "
    return next((i for i, words in _NAIVE if any(w in low for w in words)), None)


_CAPABLE = (("scorers", ("goles", "goleador", "pichichi", "marcado")), ("next_match", ("vuelve a jugar", "cuando juega", "proximo", "siguiente partido")),
            ("last_result", ("otro dia", "como quedo", "ultimo partido", "ultimo resultado")), ("matches", ("fin de semana", "partidos", "jornada")),
            ("standings", ("como va", "como anda", "como esta", "como marcha", "quien va", "quien esta", "quien lidera", "quien manda", "arriba", "primero",
                           "clasificacion", "puntos", "puesto", "posicion", "ganado mas")))


def _capable_intent(question: str, prior: list[str]) -> str | None:
    """Perfil «capaz»: entiende el lenguaje coloquial y arrastra la intención de la conversación (lo que haría un modelo sólido); sigue sin resolver
    por sí mismo qué competición/grupo se quiso decir (eso lo decide la nota de la aplicación)."""
    low = f" {norm(question)} "
    found = next((i for i, ws in _CAPABLE if any(w in low for w in ws)), None) or _naive_intent(question)
    if found:
        return found
    for text in reversed(prior):
        found = next((i for i, ws in _CAPABLE if any(w in f" {norm(text)} " for w in ws)), None) or _naive_intent(text)
        if found:
            return found
    return None


def _est(text: str) -> int:
    return max(len(text) // 4, 1)


class SimLLM:
    """Modelo obediente y determinista (ver el docstring del módulo). Una instancia por turno."""

    provider, model = "sim", "sim"

    def __init__(self, refuse_first: bool = False, interpreter: "SimInterpreter | None" = None, capable: bool = False) -> None:
        self.refuse_first = refuse_first
        self.capable = capable
        self.interp = interpreter or SimInterpreter()
        self.agent_calls = 0
        self.interp_calls = 0
        self.input_chars = 0
        self.q: str | None = None
        self.q_index = -1

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        chars = len(system) + sum(len(m.text) + sum(len(r.content) for r in m.tool_results) for m in messages)
        self.input_chars += chars
        if system.startswith(INTERPRETER_PREFIX):
            self.interp_calls += 1
            text = self.interp.answer(messages[-1].text)
            return LLMResponse(text, [], Usage(_est(system) + _est(messages[-1].text), _est(text), estimated=True))
        self.agent_calls += 1
        if self.q is None:
            self.q, self.q_index = messages[-1].text, len(messages) - 1
        text, calls = self._decide(messages)
        return LLMResponse(text, calls, Usage(chars // 4, _est(text) + 8 * len(calls), estimated=True))

    # --- decisión ---------------------------------------------------------------------------------------------------
    def _decide(self, messages: list[Message]) -> tuple[str, list[ToolCall]]:
        assert self.q is not None
        question, _, notes = self.q.partition("\n\n[")
        notes = "[" + notes if notes else ""
        results = [(m, r) for m in messages[self.q_index + 1:] if m.role == "tool" for r in m.tool_results]
        calls = [c for m in messages[self.q_index + 1:] if m.role == "assistant" for c in m.tool_calls]
        pushed = any(m.role == "user" for m in messages[self.q_index + 1:])  # reconsulta / recuperación: el modelo ya fue empujado
        if self.refuse_first and self.agent_calls == 1:
            return OFF_TOPIC_MESSAGE, []
        if results:  # ya hay datos: o sigue el plan (equipo → partidos) o redacta
            more = self._after_find_teams(calls, results, notes, question)
            return ("", more) if more else (self._compose(results, calls), [])
        if "Aclaración necesaria" in notes:
            opts = _QUOTED.findall(notes.split("opciones:", 1)[-1].split("Pregunta", 1)[0]) if "opciones:" in notes else []
            return "¿A qué competición, grupo o equipo te refieres? Opciones: " + "; ".join(f"«{o}»" for o in opts) + ". ¿Cuál prefieres?", []
        if "No existe en el catálogo" in notes:
            return "No he encontrado esa competición en el catálogo: " + "; ".join(_QUOTED.findall(notes.split("No existe en el catálogo", 1)[1])[:6]) + ".", []
        if "Temporadas anteriores" in notes:
            return "No dispongo de datos de temporadas anteriores; solo de la temporada actual.", []
        if "Revisión de la aplicación" in notes or "Revisión de la interpretación" in "".join(m.text for m in messages if m.role == "user"):
            pushed = True
        m_int = re.search(r"Intención identificada: (\w+)", "".join(m.text for m in messages if m.role == "user" and m.text) or notes)
        prior = [m.text.split("\n\n[")[0] for m in messages[: self.q_index] if m.role == "user"]
        intent = m_int.group(1) if m_int else (_capable_intent(question, prior) if self.capable else _naive_intent(question))
        if intent is None and pushed:
            intent = "standings"  # un modelo al que se le dice que SÍ es de la competición, y que «cómo va» es una clasificación
        if intent is None:
            return OFF_TOPIC_MESSAGE, []
        if intent == "rules":
            return "", [ToolCall("s1", "search_rules", {"query": question})]
        targets = [int(x) for x in _IDS.findall(self._targets_text(notes, messages))]
        team = (_QUOTED.search(notes.split("Equipo identificado:", 1)[1]) if "Equipo identificado:" in notes else None)
        if not targets and "Competición identificada" in notes:  # nota antigua: lista de grupos de la competición
            if "Falta el grupo" in notes:
                return "¿De qué grupo quieres consultarlo? Opciones: " + "; ".join(f"«{g}»" for g in re.findall(r"«([^»]+)» group_id", notes)) + ". ¿Cuál prefieres?", []
            targets = [int(x) for x in _IDS.findall(notes.split("Competición identificada", 1)[1].split(".", 1)[0])]
        if "Competiciones posibles" in notes and not targets:
            return "¿De cuál de estas competiciones te refieres? Opciones: " + "; ".join(f"«{o}»" for o in re.findall(r"«([^»]+)» \(competition_id", notes.split("Competiciones posibles", 1)[1])[:8]) + ". ¿Cuál prefieres?", []
        name = team.group(1) if team else self._team_name(question)
        if name and not targets:
            return "", [ToolCall("t1", "find_teams", {"name": name})]
        if not targets and not name:
            return "¿De qué competición y grupo quieres consultarlo?", []
        return "", self._calls_for(intent, targets, name, question)

    @staticmethod
    def _targets_text(notes: str, messages: list[Message]) -> str:
        text = "".join(m.text for m in messages if m.role == "user" and m.text.startswith(("[", "Revisión")))
        part = notes.split("Consulta estos grupos", 1)
        return (part[1].split("\n", 1)[0] if len(part) > 1 else "") + (text.split("Consulta estos grupos", 1)[1].split("\n", 1)[0] if "Consulta estos grupos" in text else "")

    @staticmethod
    def _team_name(question: str) -> str | None:
        low = norm(question)
        for n in NAMED_TEAMS:
            for tok in norm(n).split():
                if len(tok) >= 5 and tok in low.split() and tok not in ("senior", "juvenil", "equipo"):
                    return tok
        return None

    def _calls_for(self, intent: str, targets: list[int], team: str | None, question: str) -> list[ToolCall]:
        out: list[ToolCall] = []
        for k, gid in enumerate(targets):
            if intent in ("standings", "stats", "top", "bottom"):
                out.append(ToolCall(f"c{k}", "get_standings", {"group_id": gid}))
            elif intent == "scorers":
                out.append(ToolCall(f"c{k}", "get_scorer_table", {"group_id": gid, "limit": 10}))
            elif intent == "teams_list":
                out.append(ToolCall(f"c{k}", "list_teams", {"group_id": gid}))
            elif intent in ("next_match", "last_result", "matches") and not team:
                st = {"next_match": "scheduled", "last_result": "finished"}.get(intent)
                out.append(ToolCall(f"c{k}", "list_matches", {"group_id": gid, **({"status": st} if st else {}), "limit": 10}))
            elif intent in ("next_match", "last_result", "matches"):
                out.append(ToolCall(f"c{k}", "find_teams", {"name": team}))
        return out or [ToolCall("c0", "get_standings", {"group_id": targets[0]})]

    def _after_find_teams(self, calls: list[ToolCall], results: list[Any], notes: str, question: str) -> list[ToolCall]:
        last_name, last = calls[-1].name, results[-1][1]
        if last_name != "find_teams" or last.is_error:
            return []
        rows = json.loads(last.content).get("teams", [])
        if len(rows) != 1:
            return []
        row = dict(rows[0], group_id=GID[(rows[0]["competition"], rows[0]["group"])])  # el modelo no ve group_id en find_teams: lo toma del catálogo (nombre → id)
        intent = (re.search(r"Intención identificada: (\w+)", notes) or [None, _naive_intent(question)])[1]
        if intent in ("next_match", "last_result", "matches"):
            st = {"next_match": "scheduled", "last_result": "finished"}.get(intent)
            return [ToolCall("m1", "list_matches", {"group_id": row["group_id"], "team_id": row["id"], **({"status": st} if st else {}), "limit": 1})]
        if intent in ("scorers",):
            return [ToolCall("m1", "get_scorer_table", {"group_id": row["group_id"], "limit": 10})]
        return [ToolCall("m1", "get_standings", {"group_id": row["group_id"]})]

    def _compose(self, results: list[Any], calls: list[ToolCall]) -> str:
        parts: list[str] = []
        for _, r in results:
            if r.is_error:
                parts.append("No he podido consultarlo ahora.")
                continue
            d = json.loads(r.content)
            if d.get("teams") is not None and "competition" in (d["teams"][0] if d["teams"] else {"competition": 1}):
                if not d["teams"]:
                    parts.append("Ese equipo no figura en el ámbito indicado.")
                else:
                    parts.append("El equipo figura en: " + "; ".join(f"{t['competition']} ({t['group']})" for t in d["teams"]) + ". ¿Cuál quieres consultar?")
            elif d.get("teams") is not None and not d["teams"]:
                parts.append("Ningún equipo con ese nombre cumple el filtro: no figura en ese ámbito.")
            elif "standings" in d:
                top = d["standings"][0]
                parts.append(f"{d['competition']} · {d['group']}: líder {top['team']} con {top['points']} puntos.")
            elif "scorers" in d:
                parts.append(f"{d['competition']} · {d['group']}: máximo goleador {d['scorers'][0]['player']}.")
            elif "matches" in d:
                m = d["matches"][0] if d["matches"] else None
                parts.append(f"{m['home_team']} vs {m['away_team']} ({m['status']})." if m else "Sin partidos.")
            elif "hits" in d:
                parts.append("Según el reglamento, cada mitad dura 20 minutos.")
            elif "teams" in d:
                parts.append("Equipos: " + ", ".join(t["name"] for t in d["teams"][:3]) + ".")
        return "\n".join(parts) or "No he encontrado ese dato."


class SimInterpreter:
    """Sustituto del intérprete LLM: más tolerante que el resolutor determinista (erratas, ordinales, correferencias) y totalmente independiente de él."""

    OFF = ("chiste", "mundial", "champions", "tiempo hace", "2+2", "ignora", "poema", "capital de")

    def answer(self, payload_text: str) -> str:
        try:
            p = json.loads(payload_text)
        except ValueError:
            return "{}"
        q = norm(p.get("question", ""))
        cat = p.get("catalog", [])
        names = [c["competition"] for c in cat]
        state = p.get("state", {})
        hist = p.get("history", [])
        out: dict[str, Any] = {"on_topic": True, "intent": None, "competitions": [], "groups": [], "team": None, "season": None, "resolved_question": p.get("question", ""),
                               "clarification": None, "confidence": "low", "reasoning": "simulado"}
        vocab = {t for n in names for t in norm(n).split()}
        words = q.split()
        fixed = [w if w in vocab or len(w) < 5 else (difflib.get_close_matches(w, list(vocab), 1, 0.82) or [w])[0] for w in words]
        if any(o in q for o in self.OFF) and not (set(fixed) & vocab):
            out["on_topic"] = False
            out["confidence"] = "high"
            return _j(out)
        intent = None
        for key, ws in (("next_match", ("vuelve a jugar", "proximo", "cuando juega", "siguiente partido")), ("last_result", ("quedo", "otro dia", "ayer", "ultimo partido")),
                        ("scorers", ("goles", "goleador", "pichichi", "marcado")), ("matches", ("fin de semana", "partidos", "jornada")),
                        ("standings", ("clasificacion", "como va", "anda", "marcha", "manda", "arriba", "lider", "puntos", "puesto", "posicion", "ultimo"))):
            if any(w in q for w in ws):
                intent = key
                break
        out["intent"] = intent or state.get("intent")
        last_assistant = hist[-1]["assistant"] if hist else ""
        offered = [n for n in sorted(names, key=lambda n: last_assistant.upper().find(n.upper()) if n.upper() in last_assistant.upper() else 10**6)
                   if n.upper() in last_assistant.upper()]
        ordinals = {"primer": 0, "primera": 0, "primero": 0, "segund": 1, "tercer": 2, "ultim": -1}
        pick = next((offered[i] for w, i in ordinals.items() if offered and len(words) <= 4 and any(x.startswith(w) for x in words) and abs(i) < len(offered)), None)
        if pick is None and offered and len(offered) == 2 and any(x in ("otra", "otro") for x in words):
            last_comp = (state.get("competitions") or [None])[0]
            pick = next((o for o in offered if o != last_comp), None)
        age = "juvenil" if "juvenil" in fixed else "senior" if ("senior" in fixed or "aficionado" in fixed) else None
        if age is None:
            blob = norm(" ".join(h["user"] for h in hist))
            age = "juvenil" if "juvenil" in blob else None
        cands = [pick] if pick else [n for n in names if (set(fixed) & vocab) and all(w in norm(n).split() for w in fixed if w in vocab and w not in ("liga", "copa", "senior", "juvenil"))
                                     and (age != "juvenil" or "JUVENIL" in n) and (age != "senior" or "JUVENIL" not in n)]
        if pick or (0 < len(cands) <= 2 and len({c.split(" - ")[0] for c in cands}) == 1):
            out["competitions"], out["confidence"] = cands, "high" if len(cands) == 1 else "medium"
        elif cands:
            out["clarification"] = {"question": "¿A cuál te refieres?", "options": cands[:6]}
            out["confidence"] = "medium"
        elif state.get("competitions") and intent:
            out["competitions"], out["confidence"] = list(state["competitions"]), "medium"
        for t in p.get("teams", []):
            if norm(t) in q or any(w in q.split() for w in norm(t).split() if len(w) >= 5):
                out["team"] = t
                break
        if out["team"] is None and any(w in words for w in ("ese", "su", "sus", "el", "le")) and state.get("teams") and ("equipo" in words or "su" in words or "sus" in words):
            out["team"] = state["teams"][0]
        if re.search(r"\b(ano pasado|temporada pasada|anterior|20\d\d)\b", q):
            out["season"] = "previous"
        return _j(out)


# --------------------------------------------------------------------------------------------------------------------
# Ejecución y juicio
# --------------------------------------------------------------------------------------------------------------------
DATA_TOOLS = ("get_standings", "get_scorer_table", "list_matches", "list_teams", "list_rounds")
TOOL_FOR = {"standings": "get_standings", "stats": "get_standings", "top": "get_standings", "bottom": "get_standings", "scorers": "get_scorer_table",
            "last_result": "list_matches", "next_match": "list_matches", "matches": "list_matches", "teams_list": "list_teams", "rules": "search_rules"}


@dataclass
class TurnRun:
    question: str
    answer: str
    guardrail: str | None
    tools: list[tuple[str, dict[str, Any]]]
    llm_calls: int
    agent_calls: int
    interp_calls: int
    input_chars: int
    output_chars: int
    context: dict[str, Any] | None
    digest: str  # hash de todo lo que vio el LLM (para comprobar que legacy no cambia)
    error: str | None = None


def _expected_groups(exp: dict[str, Any]) -> set[int]:
    out: set[int] = set()
    for comp in exp.get("comps") or []:
        wanted = exp.get("groups")
        for g in REAL[comp]:
            if wanted in (None, "all") or g in wanted:
                out.add(GID[(comp, g)])
    return out


def judge(exp: dict[str, Any], run: TurnRun) -> dict[str, Any]:
    action = exp["action"]
    data = [(n, a) for n, a in run.tools if n in DATA_TOOLS and isinstance(a.get("group_id"), int)]
    consulted = {a["group_id"] for _, a in data}
    answer, low = run.answer, norm(run.answer)
    refusal = answer.strip() == OFF_TOPIC_MESSAGE
    clar = not data and not refusal and "?" in answer
    mentioned = {c for c in REAL if norm(c) in low}
    flags: dict[str, bool] = {"critical": False, "refusal_wrong": False, "unneeded_clarification": False, "missed_clarification": False, "false_positive": False,
                              "blocked": False, "wrong_comp": False}
    ok = False
    if action in ("act", "assume") and exp.get("intent") == "rules":
        flags["refusal_wrong"] = refusal
        ok = any(n == "search_rules" for n, _ in run.tools) and not data
    elif action in ("act", "assume"):
        want = _expected_groups(exp)
        tool = TOOL_FOR.get(exp.get("intent", ""), None)
        flags["wrong_comp"] = bool(consulted - want)
        flags["critical"] = flags["wrong_comp"]
        flags["refusal_wrong"] = refusal
        flags["unneeded_clarification"] = clar
        flags["blocked"] = bool(re.search(r"no figura|ningun equipo", low)) and not data
        tools_ok = tool is None or any(n == tool for n, _ in run.tools)
        team_ok = True
        if exp.get("team") and exp.get("intent") in ("next_match", "last_result"):
            team_ok = any(n == "list_matches" and a.get("team_id") in {_team_id(exp["team"], w) for w in NAMED_TEAMS[exp["team"]]} for n, a in run.tools)
        coherent = all(c in mentioned for c in exp.get("comps") or []) if exp.get("intent") in ("standings", "stats", "scorers") else True
        extra = bool(mentioned - set(exp.get("comps") or [])) if exp.get("comps") and data else False
        ok = bool(data) and consulted == want and tools_ok and team_ok and coherent and not extra
        flags["wrong_comp"] = flags["wrong_comp"] or extra
    elif action == "ask":
        opts = exp.get("options") or []
        flags["missed_clarification"] = bool(data)
        flags["refusal_wrong"] = refusal
        outside = {c for c in mentioned if c not in opts and not any(norm(o) == norm(c) for o in opts)} if any(o in REAL for o in opts) else set()
        if data and exp.get("comps_for_critical"):
            flags["critical"] = bool(consulted - _expected_groups({"comps": exp["comps_for_critical"]}))
        ok = clar and all(norm(o) in low for o in opts) and not outside
    elif action == "missing":
        flags["false_positive"] = bool(data)
        flags["refusal_wrong"] = refusal
        ok = not data and not refusal and bool(re.search(r"no (he )?encontrad|no existe|no figura|no (consta|dispongo|tengo)|no hay", low))
    elif action == "nodata":
        flags["false_positive"] = bool(data)
        flags["refusal_wrong"] = refusal
        ok = not data and not refusal and bool(re.search(r"anterior|pasad|historic|no dispongo|no hay datos|no tengo", low))
    elif action == "refuse":
        flags["false_positive"] = bool(data)
        ok = refusal and not data
    return {"ok": ok, **flags, "outcome": "refusal" if refusal else "data" if data else "clarification" if clar else "statement", "consulted": sorted(consulted)}


def _supports_state(agent: Agent) -> bool:
    return "state" in inspect.signature(agent.run).parameters


async def _run_turn(mode: str, question: str, history: list[Turn], focus: dict[str, Any], state: Any, refuse_first: bool,
                    capable: bool = False) -> tuple[Any, TurnRun, SimLLM]:
    previous = os.environ.get("AGENT_CONTEXT")  # por entorno (y no por parámetro) para poder medir también código anterior a `AGENT_CONTEXT`; se restaura siempre
    os.environ["AGENT_CONTEXT"] = mode
    try:
        return await _run_turn_env(mode, question, history, focus, state, refuse_first, capable)
    finally:
        if previous is None:
            os.environ.pop("AGENT_CONTEXT", None)
        else:
            os.environ["AGENT_CONTEXT"] = previous


async def _run_turn_env(mode: str, question: str, history: list[Turn], focus: dict[str, Any], state: Any, refuse_first: bool,
                        capable: bool) -> tuple[Any, TurnRun, SimLLM]:
    llm = SimLLM(refuse_first=refuse_first, capable=capable)
    seen: list[str] = []
    orig = llm.complete

    def spy(system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        seen.append(hashlib.sha1(json.dumps([system, [(m.role, m.text, [(c.name, c.arguments) for c in m.tool_calls], [r.content for r in m.tool_results]) for m in messages],
                                             [t.get("name") for t in tools]], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12])
        return orig(system, messages, tools)

    llm.complete = spy  # type: ignore[method-assign]
    mcp = FakeMcp()
    agent = Agent(llm, mcp, 8, router_shadow=False)
    kwargs: dict[str, Any] = {"focus": focus, "conversation_id": "eval"}
    if _supports_state(agent):
        kwargs["state"] = state
    res = await agent.run(question, select_turns(history), **kwargs)
    rec = res.run
    tools = [(c.name, dict(c.arguments)) for c in (rec.tool_calls if rec else []) if not c.refused]
    out_chars = len(res.answer)
    run = TurnRun(question, res.answer, res.guardrail, tools, len(rec.llm_calls) if rec else 0, llm.agent_calls, llm.interp_calls, llm.input_chars, out_chars,
                  getattr(rec, "context", None), hashlib.sha1("|".join(seen).encode()).hexdigest()[:16], res.error)
    return res, run, llm


def run_case(mode: str, case: dict[str, Any], capable: bool = False) -> list[dict[str, Any]]:
    """Ejecuta una conversación turno a turno como lo haría `web.py` (historial, foco, estado) y devuelve el juicio de cada turno evaluado."""
    history: list[Turn] = [Turn(q, a) for q, a in case.get("history", [])]
    focus: dict[str, Any] = dict(case.get("focus", {}))
    state: Any = None
    out: list[dict[str, Any]] = []
    for i, t in enumerate(case["turns"]):
        use_state = None if (t.get("lose_memory") or i == 0 and case.get("history")) else state
        res, run, _ = asyncio.run(_run_turn(mode, t["q"], history, focus, use_state, bool(t.get("refuse_first")), capable))
        if not res.error and res.answer.strip() not in ("", OFF_TOPIC_MESSAGE) and res.guardrail in MEMORY_GUARDRAILS:
            history.append(Turn(t["q"], res.answer))
        if res.run is not None and not res.error:
            focus = merge_focus(focus, compute_focus(res.run, res.names, res.groups_used, res.official, t["q"]))
        state = getattr(res, "state", None) or state
        exp = t.get("expect")
        if exp:
            verdict = judge(exp, run)
            declared = bool(re.search(r"entiendo que", norm(run.answer)))
            out.append({"case": case["id"], "turn": i + 1, "cat": case["cat"], "q": t["q"], "action": exp["action"], **verdict, "declared": declared,
                        "llm_calls": run.llm_calls, "agent_calls": run.agent_calls, "interp_calls": run.interp_calls, "input_chars": run.input_chars,
                        "digest": run.digest, "answer": run.answer[:160], "tools": [f"{n}({a.get('group_id', a.get('name', ''))})" for n, a in run.tools],
                        "ctx": (run.context or {}).get("status")})
    return out


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def rate(sel: list[dict[str, Any]], key: str = "ok") -> float | None:
        return round(sum(1 for r in sel if r[key]) / len(sel), 4) if sel else None
    act = [r for r in rows if r["action"] in ("act", "assume")]
    ask = [r for r in rows if r["action"] == "ask"]
    neg = [r for r in rows if r["action"] in ("missing", "nodata", "refuse")]
    convs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        convs[r["case"]].append(r)
    multi = [v for v in convs.values() if len(v) > 1]
    by_cat: dict[str, dict[str, Any]] = {}
    for cat in sorted({r["cat"] for r in rows}):
        sel = [r for r in rows if r["cat"] == cat]
        by_cat[cat] = {"n": len(sel), "ok": sum(1 for r in sel if r["ok"]), "critical": sum(1 for r in sel if r["critical"])}
    return {
        "turns": len(rows), "cases": len(convs), "multi_turn_cases": len(multi), "pass_rate": rate(rows),
        "conversation_pass_rate": round(sum(1 for v in convs.values() if all(r["ok"] for r in v)) / len(convs), 4) if convs else None,
        "multi_turn_pass_rate": round(sum(1 for v in multi if all(r["ok"] for r in v)) / len(multi), 4) if multi else None,
        "critical_wrong_competition": sum(1 for r in rows if r["critical"]),
        "competition_precision_act": rate(act), "ask_correct": rate(ask), "negative_correct": rate(neg),
        "refusals_wrong": sum(1 for r in rows if r["refusal_wrong"]), "unneeded_clarifications": sum(1 for r in rows if r["unneeded_clarification"]),
        "missed_clarifications": sum(1 for r in rows if r["missed_clarification"]), "false_positives": sum(1 for r in rows if r["false_positive"]),
        "blocked_by_context": sum(1 for r in rows if r["blocked"]),
        "assumption_declared_rate": rate([r for r in rows if r["action"] == "assume"], "declared"),
        "llm_calls_per_turn": round(sum(r["llm_calls"] for r in rows) / len(rows), 3) if rows else None,
        "interpreter_calls_total": sum(r["interp_calls"] for r in rows),
        "est_input_tokens_per_turn": round(sum(r["input_chars"] for r in rows) / 4 / len(rows)) if rows else None,
        "by_category": by_cat, "failures": [{k: r[k] for k in ("case", "turn", "q", "action", "outcome", "tools", "answer", "critical")} for r in rows if not r["ok"]]}


def load_cases(which: str = "dev") -> list[dict[str, Any]]:
    from futsal.agent.understanding_cases import CASES
    from futsal.agent.understanding_holdout import HOLDOUT

    return {"dev": CASES, "holdout": HOLDOUT, "all": [*CASES, *HOLDOUT]}[which]


def evaluate(modes: tuple[str, ...] = MODES, cases: list[dict[str, Any]] | None = None, only: Callable[[dict[str, Any]], bool] | None = None,
             capable: bool = False, which: str = "dev") -> dict[str, Any]:
    todo = [c for c in (cases if cases is not None else load_cases(which)) if only is None or only(c)]
    result: dict[str, Any] = {"cases": len(todo), "modes": {}, "rows": {}}
    for mode in modes:
        rows = [r for case in todo for r in run_case(mode, case, capable)]
        result["modes"][mode] = aggregate(rows)
        result["rows"][mode] = rows
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--out", default="")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--set", dest="which", choices=("dev", "holdout", "all"), default="dev", help="dev: conjunto de desarrollo; holdout: conjunto ciego")
    ap.add_argument("--profile", choices=("strict", "capable"), default="strict", help="strict: el modelo simulado solo reconoce palabras clave; capable: entiende lo coloquial y arrastra la intención")
    args = ap.parse_args(argv)
    res = evaluate(tuple(m for m in args.modes.split(",") if m), capable=args.profile == "capable", which=args.which)
    for mode, agg in res["modes"].items():
        head = {k: v for k, v in agg.items() if k not in ("by_category", "failures")}
        print(f"== {mode}: {json.dumps(head, ensure_ascii=False)}")
        if args.verbose:
            for f in agg["failures"]:
                print("   FALLO", json.dumps(f, ensure_ascii=False))
    if args.out:
        slim = {"cases": res["cases"], "modes": res["modes"], "digests": {m: {f"{r['case']}#{r['turn']}": r["digest"] for r in rows} for m, rows in res["rows"].items()}}
        Path(args.out).write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
