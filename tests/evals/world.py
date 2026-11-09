"""Mundo de pruebas: catálogo del snapshot sintético, casos de `dev` resueltos y un LLM simulado de guion fijo."""

import json
from dataclasses import dataclass
from typing import Any

from futsal.agent.agent import OFF_TOPIC_MESSAGE, PROMPT_SETS
from futsal.agent.llm import LLMResponse, Message, ToolCall, Usage
from futsal.evals.catalog import Catalog
from futsal.evals.datasets import load_cases
from futsal.evals.selector import ResolvedCase, ResolvedTurn, resolve_case
from futsal.evals.snapshot import parse_as_of

AS_OF = "2026-10-20T12:00:00"


@dataclass
class World:
    catalog: Catalog
    resolved: dict[str, ResolvedCase]

    def rc(self, case_id: str) -> ResolvedCase:
        r = self.resolved[case_id]
        assert r.status == "ok", (case_id, r.reason)
        return r

    def turn(self, case_id: str, i: int = 0) -> ResolvedTurn:
        return self.rc(case_id).turns[i]

    def b(self, case_id: str, name: str) -> dict[str, Any]:
        return self.rc(case_id).bindings[name]


def build_world(synthetic: Any, split: str = "dev") -> World:
    cat = Catalog.load(synthetic.engine, seed=synthetic.manifest["fingerprint"]["sha256"], as_of=parse_as_of(AS_OF))
    cases = load_cases(synthetic.root, split)
    caps = cat.capabilities()
    return World(cat, {c.id: resolve_case(c, cat, caps) for c in cases})


# ----------------------------------------------------------------------------- cargas útiles de herramientas (lo que «devolvería» el MCP)
def std_payload(name: str, points: int) -> dict[str, Any]:
    return {"standings": [{"team": name, "points": points, "position": 1}]}


def match_payload(home: str, away: str, h: int, a: int) -> dict[str, Any]:
    return {"matches": [{"home_team": home, "away_team": away, "home_score": h, "away_score": a, "status": "finished"}]}


def rule_payload(rule: int, pages: str, text: str) -> dict[str, Any]:
    return {"hits": [{"rule": rule, "pages": pages, "text": text, "document": "fifa-futsal-laws-2025-26", "rule_title": "Duración del partido"}],
            "count": 1}


def system_prompt_leak() -> str:
    return " ".join(PROMPT_SETS["legacy"].system.split()[:40])


# ----------------------------------------------------------------------------- LLM simulado de guion fijo
class ScriptedLLM:
    """Responde según un guion fijo: `script[pregunta] = (llamadas a herramientas, texto final)`. Sin red; tokens del proveedor simulados.

    Sin entrada en el guion responde con una abstención neutra. Cada llamada cuenta como una petición (`calls`)."""

    provider, model = "scripted", "scripted-1"

    def __init__(self, script: dict[tuple[str, str], tuple[list[tuple[str, dict[str, Any]]], str]], usage: tuple[int, int] = (900, 40),
                 fail_after: int | None = None, fail_with: Exception | None = None) -> None:
        self.script, self.usage, self.calls = script, usage, 0
        self.fail_after, self.fail_with = fail_after, fail_with

    def _key(self, messages: list[Message]) -> tuple[str, str]:
        """(primera pregunta de la conversación, pregunta actual). La actual es la última entrada del guion: la aplicación añade mensajes
        de «user» propios (recordatorios de herramientas) que no son preguntas."""
        user = [m.text.split("\n\n[Nota de la aplicación")[0].strip() for m in messages if m.role == "user"]
        for text in reversed(user):
            if (user[0], text) in self.script:
                return user[0], text
        return user[0], user[-1]

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        if self.fail_after is not None and self.calls >= self.fail_after and self.fail_with is not None:
            raise self.fail_with
        self.calls += 1
        steps, text = self.script.get(self._key(messages), ([], NO_DATA))
        done = sum(1 for m in messages if m.role == "tool")
        if done < len(steps):
            name, args = steps[done]
            return LLMResponse("", [ToolCall(f"c{done}", name, args)], Usage(self.usage[0], self.usage[1], 0, None))
        return LLMResponse(text, [], Usage(self.usage[0] + 100 * done, self.usage[1] + 20, 0, None))


def dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, sort_keys=True)


# ----------------------------------------------------------------------------- guion «oráculo» de extremo a extremo
NO_DATA = "No he encontrado ese dato en las fuentes que tengo disponibles."
RULE_ANSWERS = {"regla_duracion_mitad": "Cada mitad dura 20 minutos (Regla 7, página 60)."}


def _group_id(rc: ResolvedCase) -> int | None:
    for b in rc.bindings.values():
        if isinstance(b, dict) and isinstance(b.get("group_id"), int):
            return int(b["group_id"])
    return None


def _fact_text(f: dict[str, Any]) -> str:
    kind, value = f["kind"], f.get("value")
    if kind == "position":
        return f"puesto {value} ({f.get('team', '')})"
    if kind == "number":
        return f"{f.get('label', '')} {value}"
    if kind == "names":
        return ", ".join(str(v) for v in value)
    if kind == "text_any":
        return str(f["any"][0])
    return str(value)


def oracle_script(resolved: dict[str, ResolvedCase]) -> dict[tuple[str, str], tuple[list[tuple[str, dict[str, Any]]], str]]:
    """Guion fijo: para cada turno, llama a la herramienta necesaria y responde con los hechos esperados (o aclara, se abstiene o rechaza)."""
    script: dict[tuple[str, str], tuple[list[tuple[str, dict[str, Any]]], str]] = {}
    for rc in resolved.values():
        if rc.status != "ok":
            continue
        gid = _group_id(rc)
        reported = next((b["reported_match"]["match_id"] for b in rc.bindings.values() if isinstance(b, dict) and b.get("reported_match")), None)
        for t in rc.turns:
            steps: list[tuple[str, dict[str, Any]]] = []
            for need in t.tools_needed:
                name = need.split("|")[0]
                if name == "search_rules":
                    steps.append((name, {"query": "mitades duración partido", "limit": 5}))
                elif name in ("get_match_report", "list_match_events") and reported is not None:
                    steps.append(("get_match_report", {"match_id": reported}))
                elif gid is not None:
                    steps.append(("list_matches" if name in ("get_match_report", "list_match_events") else name, {"group_id": gid}))
            if not steps and t.behavior != "offtopic":
                steps.append(("list_competitions", {}))
            if t.rule_case in RULE_ANSWERS:
                text = RULE_ANSWERS[t.rule_case]
            elif t.behavior == "clarify":
                text = "Hay varias opciones: " + "; ".join(t.clarify_mentions) + ". ¿A cuál te refieres?"
            elif t.behavior == "abstain":
                text = NO_DATA
            elif t.behavior == "offtopic":
                text = OFF_TOPIC_MESSAGE
            else:
                text = "; ".join(_fact_text(f) for f in t.facts) + "."
            script[(rc.turns[0].user, t.user)] = (steps, text)
    return script


VOLATILE = {"timing", "run_id", "generated_at", "latency"}


def normalize(o: Any) -> Any:
    """Quita marcas de tiempo, duraciones e IDs de ejecución para comparar resultados byte a byte."""
    if isinstance(o, dict):
        return {k: normalize(v) for k, v in o.items() if k not in VOLATILE}
    if isinstance(o, list):
        return [normalize(v) for v in o]
    return o
