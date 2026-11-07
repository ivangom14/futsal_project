"""Intérprete LLM CONDICIONAL (modo `llm`): una llamada extra, sin herramientas, que entiende la pregunta con TODO el contexto cuando el resolutor determinista
no tiene confianza (referencias, ordinales, empates, coloquial, sin vocabulario reconocido).

Reglas de seguridad: solo INTERPRETA (nunca responde ni inventa datos). Todo lo que devuelve se VALIDA contra el catálogo real: una competición, grupo o
equipo que no exista se descarta; la intención debe ser una de las conocidas. Un fallo, una salida inválida o un LLM caído devuelven `None` y el flujo sigue con
la interpretación determinista (nunca rompe la pregunta).
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from futsal.agent.convstate import ConversationState
from futsal.agent.intent import INTENTS
from futsal.agent.llm import LLMClient, Message
from futsal.agent.memory import Turn, clip
from futsal.domain.competitions import Competition, fold

SYSTEM = (
    "INTERPRETE_RFFM. Eres el intérprete de preguntas de un asistente de fútbol sala de la RFFM. NO respondes a la pregunta ni inventas datos: solo dices qué "
    "quiere el usuario y a qué entidades del CATÁLOGO se refiere. Recibes un JSON con: question, history (turnos anteriores, con la respuesta del asistente), "
    "state (lo identificado en la conversación: competiciones, categoría, grupo, equipos, opciones ofrecidas, intención), catalog (las únicas competiciones y "
    "grupos que existen), teams (nombres de equipo candidatos) y deterministic (lo que el resolutor automático ya entendió y por qué no está seguro). "
    "Usa el historial para resolver referencias («la otra», «el segundo», «ese equipo», «¿y la copa?») y mantén la categoría de la conversación si es "
    "razonable, pero un cambio de tema, equipo, categoría o competición prevalece siempre sobre el contexto. Responde SOLO con un JSON: "
    '{"on_topic": bool (¿trata de competiciones, equipos, partidos, clasificaciones, goleadores o reglas de fútbol sala?), '
    f'"intent": una de {list(INTENTS)} o null, "competitions": [nombres EXACTOS del catálogo], "groups": [nombres exactos de grupo], '
    '"team": nombre exacto de la lista teams o null, "season": "previous" si pide otra temporada, "resolved_question": la pregunta reescrita de forma '
    'autónoma, "clarification": {"question": texto, "options": [nombres exactos]} si hay varias interpretaciones razonables y ninguna es claramente '
    'mejor, o null, "confidence": "high"|"medium"|"low"}. Si no existe en el catálogo lo que pide, deja competitions vacío. Nunca inventes nombres.'
)
MAX_HISTORY = 6


@dataclass
class Interpretation:
    on_topic: bool = True
    intent: str | None = None
    competitions: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    team: str | None = None
    season: str | None = None
    resolved_question: str = ""
    options: list[str] = field(default_factory=list)  # clarification.options, validadas
    clarification: str = ""
    confidence: str = "low"
    discarded: list[str] = field(default_factory=list)  # lo que el modelo dijo y NO existe (auditoría)

    def to_dict(self) -> dict[str, Any]:
        return {"on_topic": self.on_topic, "intent": self.intent, "competitions": self.competitions, "groups": self.groups, "team": self.team,
                "season": self.season, "options": self.options, "confidence": self.confidence, "discarded": self.discarded}


def catalog_payload(competitions: Sequence[Competition], classify_kind: Any) -> list[dict[str, Any]]:
    return [{"competition": c.name, "groups": [g for _, g in c.groups], **classify_kind(c)} for c in competitions]


def build_payload(question: str, turns: Sequence[Turn], state: ConversationState, catalog: list[dict[str, Any]], teams: list[str], deterministic: dict[str, Any]) -> str:
    history = [{"user": clip(t.question, 300), "assistant": clip(t.answer, 500)} for t in list(turns)[-MAX_HISTORY:]]
    state_view = {k: v for k, v in state.to_dict().items() if k in ("competitions", "groups", "age", "kind", "teams", "offered", "offered_kind", "intent", "pending") and v}
    return json.dumps({"question": question, "history": history, "state": state_view, "catalog": catalog, "teams": teams, "deterministic": deterministic},
                      ensure_ascii=False, separators=(",", ":"))


def _extract_json(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def validate(raw: dict[str, Any], competitions: Sequence[Competition], teams: Sequence[str]) -> Interpretation:
    """Salida del LLM → `Interpretation` con SOLO entidades que existen (comparación sin acentos ni mayúsculas)."""
    by_comp = {fold(c.name): c for c in competitions}
    by_team = {fold(t): t for t in teams}
    out = Interpretation(on_topic=raw.get("on_topic") is not False, resolved_question=str(raw.get("resolved_question") or "")[:400],
                         confidence=str(raw["confidence"]) if raw.get("confidence") in ("high", "medium", "low") else "low")
    intent = raw.get("intent")
    out.intent = intent if intent in INTENTS else None
    if intent and out.intent is None:
        out.discarded.append(f"intent:{intent}")
    for name in raw.get("competitions") or []:
        comp = by_comp.get(fold(str(name)))
        if comp and comp.name not in out.competitions:
            out.competitions.append(comp.name)
        elif not comp:
            out.discarded.append(f"competition:{str(name)[:60]}")
    valid_groups = {fold(g): g for c in competitions if c.name in out.competitions or not out.competitions for _, g in c.groups}
    for name in raw.get("groups") or []:
        if fold(str(name)) in valid_groups:
            out.groups.append(valid_groups[fold(str(name))])
        else:
            out.discarded.append(f"group:{str(name)[:40]}")
    team = raw.get("team")
    if team:
        if fold(str(team)) in by_team:
            out.team = by_team[fold(str(team))]
        else:
            out.discarded.append(f"team:{str(team)[:60]}")
    out.season = "previous" if raw.get("season") == "previous" else None
    clar = raw.get("clarification")
    if isinstance(clar, dict):
        opts = [by_comp[fold(str(o))].name if fold(str(o)) in by_comp else by_team.get(fold(str(o)), "") for o in clar.get("options") or []]
        out.options = [o for o in dict.fromkeys(opts) if o]
        out.clarification = str(clar.get("question") or "")[:200]
    return out


def interpret(llm: LLMClient, payload: str, competitions: Sequence[Competition], teams: Sequence[str]) -> tuple[Interpretation | None, Any]:
    """(interpretación validada o None, respuesta cruda del LLM para contabilizar tokens). Lanza solo lo que lance el cliente (`LLMError`): quien llama lo captura."""
    resp = llm.complete(SYSTEM, [Message("user", payload)], [])
    data = _extract_json(re.sub(r"^```(?:json)?|```$", "", resp.text.strip(), flags=re.MULTILINE))
    return (validate(data, competitions, teams) if data is not None else None), resp
