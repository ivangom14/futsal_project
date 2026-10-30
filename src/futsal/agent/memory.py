"""Memoria conversacional (Fase 23): selección acotada del historial y almacén en memoria por conversación.

El historial es contexto LINGÜÍSTICO para resolver referencias («su equipo», «el siguiente partido»). No es fuente de datos
deportivos ni de identificadores: el agente no extrae IDs de él y vuelve a consultar las tools en cada pregunta.
"""

import re
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from futsal.agent.criteria import SCOPE_WORDS, norm
from futsal.agent.llm import Message

DEFAULT_MAX_TURNS = 6  # parejas pregunta/respuesta enviadas al LLM
DEFAULT_MAX_CHARS = 6000  # ≈ 1.500 tokens de historial como máximo
MAX_MESSAGE_CHARS = 1200  # una respuesta larga se recorta (los nombres para resolver referencias van al principio)
MAX_STORED_TURNS = 20  # por conversación en el servidor
MAX_CONVERSATIONS = 200  # conversaciones en memoria (LRU)
MERGE_MAX_WORDS = 12  # una respuesta a una aclaración es corta; algo más largo se trata como pregunta nueva
ASK_WORDS = SCOPE_WORDS | {"equipo", "jornada", "jugador", "partido"}
CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Turn:
    question: str
    answer: str


def clip(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def select_turns(turns: Sequence[Turn], max_turns: int = DEFAULT_MAX_TURNS, max_chars: int = DEFAULT_MAX_CHARS) -> list[Turn]:
    """Los turnos más recientes que caben en `max_turns` y `max_chars` (recortados), en orden cronológico."""
    chosen: list[Turn] = []
    used = 0
    for turn in reversed(turns):
        item = Turn(clip(turn.question), clip(turn.answer))
        size = len(item.question) + len(item.answer)
        if len(chosen) >= max_turns or (chosen and used + size > max_chars) or size > max_chars:
            break
        chosen.append(item)
        used += size
    return chosen[::-1]


def history_messages(turns: Iterable[Turn]) -> list[Message]:
    """Mensajes `user`/`assistant` alternados (sin tools: solo texto)."""
    out: list[Message] = []
    for t in turns:
        out += [Message("user", t.question), Message("assistant", t.answer)]
    return out


def turns_from_client(items: Iterable[tuple[str, str]]) -> list[Turn]:
    """Pares (rol, texto) enviados por el navegador → turnos completos user→assistant. Descarta roles desconocidos, textos vacíos y
    mensajes sin su pareja; un último mensaje de usuario sin respuesta (la pregunta en curso) no forma turno, así no se duplica."""
    turns: list[Turn] = []
    pending: str | None = None
    for role, text in items:
        text = text.strip()
        if not text:
            pending = None
        elif role == "user":
            pending = text
        elif role == "assistant" and pending is not None:
            turns.append(Turn(pending, text))
            pending = None
        else:
            pending = None
    return turns[-MAX_STORED_TURNS:]


def asks_for_input(answer: str) -> bool:
    """¿La respuesta del asistente termina pidiendo un dato (competición, grupo, equipo, jornada…)? Una pregunta de cortesía
    («¿Quieres algo más?») no cuenta."""
    lines = [ln.strip() for ln in answer.strip().splitlines() if ln.strip()]
    return bool(lines) and lines[-1].endswith("?") and any(w in ASK_WORDS for w in norm(lines[-1]).split())


def is_reply(text: str) -> bool:
    t = text.strip()
    return bool(t) and not t.endswith("?") and not t.startswith("¿") and len(t.split()) <= MERGE_MAX_WORDS


def resolve_pending(turns: Sequence[Turn], question: str) -> tuple[list[Turn], str, bool]:
    """Si el asistente pidió un dato y el usuario responde brevemente, une la pregunta original con SUS precisiones (solo texto del
    usuario, nunca del asistente) para no depender de que el modelo recuerde la pregunta pendiente. Devuelve (turnos que quedan como
    historial, pregunta efectiva, ¿se reescribió?)."""
    if not turns or not asks_for_input(turns[-1].answer) or not is_reply(question):
        return list(turns), question, False
    k = len(turns) - 1
    while k > 0 and asks_for_input(turns[k - 1].answer) and is_reply(turns[k].question):
        k -= 1
    replies = [t.question for t in turns[k + 1 :]] + [question]
    merged = f"{turns[k].question} (Precisiones del usuario: {'; '.join(replies)})"
    return list(turns[:k]), merged, True


class ConversationMemory:
    """Historial por `conversation_id` en el servidor (acotado, sin persistencia en disco: se pierde al reiniciar y el navegador
    puede volver a sembrarlo). Escrito solo con turnos que el propio servidor produjo."""

    def __init__(self, max_conversations: int = MAX_CONVERSATIONS) -> None:
        self._data: OrderedDict[str, list[Turn]] = OrderedDict()
        self._max = max_conversations

    def get(self, conversation_id: str) -> list[Turn] | None:
        turns = self._data.get(conversation_id)
        if turns is not None:
            self._data.move_to_end(conversation_id)
        return None if turns is None else list(turns)

    def put(self, conversation_id: str, turns: Sequence[Turn]) -> None:
        self._data[conversation_id] = list(turns)[-MAX_STORED_TURNS:]
        self._data.move_to_end(conversation_id)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def forget(self, conversation_id: str) -> bool:
        return self._data.pop(conversation_id, None) is not None

    def __len__(self) -> int:
        return len(self._data)
