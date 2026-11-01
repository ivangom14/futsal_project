"""Valoración de respuestas (Futsito, Fase 25): registro en un JSONL local (`data/feedback/feedback.jsonl`, ignorado por Git).

Solo se guarda lo necesario para analizar y mejorar: conversación, mensaje, valoración, motivo, comentario, fecha y un resumen del
diagnóstico (modelo, herramientas, guardarraíl, contexto…). Sin claves, sin texto de la respuesta y sin IDs internos.
No cambia el comportamiento del agente: es información para análisis posterior."""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from futsal.agent.memory import CONVERSATION_ID
from futsal.agent.trace import redact

REASONS = ("wrong_data", "lost_context", "incomplete", "bad_source", "misunderstood", "other")
REASON_LABELS = {"wrong_data": "Dato incorrecto", "lost_context": "Ha perdido el contexto", "incomplete": "Respuesta incompleta",
                 "bad_source": "Fuente incorrecta o no respaldada", "misunderstood": "Ha interpretado mal la pregunta", "other": "Otro"}
MAX_COMMENT = 1000
MAX_SUMMARIES = 200
_lock = threading.Lock()


class FeedbackRequest(BaseModel):
    conversation_id: str = Field(pattern=CONVERSATION_ID.pattern)
    message_id: str = Field(pattern=CONVERSATION_ID.pattern)  # id del mensaje de la respuesta valorada
    rating: Literal["up", "down"]
    reason: Literal["wrong_data", "lost_context", "incomplete", "bad_source", "misunderstood", "other"] | None = None
    comment: str | None = Field(default=None, max_length=MAX_COMMENT)
    run_id: str | None = Field(default=None, max_length=32, pattern=r"^[A-Za-z0-9_-]*$")


class RunSummaries:
    """Resumen de diagnóstico de las últimas respuestas (en memoria, acotado) para adjuntarlo a una valoración sin fiarse del navegador."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def add(self, run_id: str, summary: dict[str, Any]) -> None:
        self._data[run_id] = summary
        while len(self._data) > MAX_SUMMARIES:
            self._data.pop(next(iter(self._data)))

    def get(self, run_id: str | None) -> dict[str, Any] | None:
        return self._data.get(run_id) if run_id else None


def record(path: Path, fb: FeedbackRequest, summary: dict[str, Any] | None, now: datetime | None = None) -> dict[str, Any]:
    """Añade una valoración al JSONL. Un motivo solo vale con 👎; el comentario se limpia y se enmascaran secretos."""
    comment = redact((fb.comment or "").strip())[:MAX_COMMENT] or None
    entry = {"ts": (now or datetime.now(UTC)).isoformat(), "conversation_id": fb.conversation_id, "message_id": fb.message_id,
             "rating": fb.rating, "reason": fb.reason if fb.rating == "down" else None,
             "comment": comment if fb.rating == "down" else None, "run_id": fb.run_id, "diagnostics": summary}
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
