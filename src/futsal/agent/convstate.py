"""Estado conversacional ESTRUCTURADO (Futsito, modo `deterministic`/`llm`).

Separación de responsabilidades (sin duplicar ni contradecir):
- historial (`memory.Turn`): texto lingüístico de los turnos; fuente de verdad del estado.
- foco (`presentation.compute_focus`): lo que las tools consultaron de verdad (equipo, competición, grupo…).
- estado (esto): lo que la conversación ha IDENTIFICADO (competiciones, categoría, grupo, equipos, opciones ofrecidas, intención, pregunta pendiente).
El estado es una caché derivable: si no coincide con el último turno del historial, o el servidor perdió su memoria, se RECONSTRUYE desde el historial
(`context.ContextEngine.rebuild`); nunca manda sobre él.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from futsal.agent.memory import Turn, clip

VERSION = 1
MAX_TEAMS = 5
MAX_MENTIONED = 12


def turn_hash(question: str, answer: str) -> str:
    return hashlib.sha1(f"{clip(question)}\x00{clip(answer)}".encode()).hexdigest()[:12]


@dataclass
class ConversationState:
    version: int = VERSION
    last_turn: str = ""  # hash del último turno (pregunta+respuesta) del que se derivó: coherencia con el historial
    competitions: list[str] = field(default_factory=list)  # última(s) competición(es) identificada(s), nombres oficiales
    groups: list[str] = field(default_factory=list)  # grupo(s) concreto(s) de esa competición (vacío = todos)
    age: str | None = None
    kind: str | None = None
    teams: list[str] = field(default_factory=list)  # equipos relevantes, el más reciente primero
    offered: list[str] = field(default_factory=list)  # opciones que Futsito acaba de ofrecer (competiciones, grupos o equipos)
    offered_kind: str | None = None  # competition | group | team
    intent: str | None = None
    pending: dict[str, Any] | None = None  # {"question": pregunta que espera aclaración, "kind": ...}
    mentioned: list[dict[str, Any]] = field(default_factory=list)  # [{"type": "competition"|"team", "name": …, "turn": n}]
    declared: list[str] = field(default_factory=list)  # competiciones cuya suposición ya se comunicó (no se repite)
    turns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> "ConversationState | None":
        if not isinstance(raw, dict) or raw.get("version") != VERSION:
            return None
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw}
        try:
            return cls(**known)
        except TypeError:
            return None

    def remember(self, kind: str, name: str) -> None:
        self.mentioned = [m for m in self.mentioned if not (m.get("type") == kind and m.get("name") == name)]
        self.mentioned.append({"type": kind, "name": name, "turn": self.turns})
        self.mentioned = self.mentioned[-MAX_MENTIONED:]

    def remember_team(self, name: str) -> None:
        self.teams = [name, *[t for t in self.teams if t != name]][:MAX_TEAMS]
        self.remember("team", name)


def is_current(state: ConversationState | None, turns: Sequence[Turn]) -> bool:
    """¿El estado guardado corresponde al historial que llega? Sin turnos no hay conversación (un estado huérfano se ignora)."""
    return bool(state and turns and state.last_turn == turn_hash(turns[-1].question, turns[-1].answer))
