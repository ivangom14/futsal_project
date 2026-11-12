"""Registro ligero de preguntas reales del chat: un JSONL por día (`logs/questions/AAAA-MM-DD.jsonl`, ignorado por Git).

Cada línea: `{"ts": "<UTC ISO de llegada>", "question": "<texto saneado>"}` y, solo con `QUESTION_LOG_ANSWERS=on`, `"answer"` (texto
saneado mostrado al usuario; `null` si la petición falló). Nada más (ni historial, ni datos de MCP, ni prompts, ni tokens).
`POST /chat` solo encola al terminar la petición (`put_nowait` en una cola acotada): un único hilo escritor sanea, escribe y aplica la retención.
Cola llena, disco lleno o cualquier error → la pregunta se descarta y el agente sigue igual. Para eliminarlo: borrar este
módulo y sus dos líneas en `web.py`.

Entorno: `QUESTION_LOG=on|off` (off por defecto), `QUESTION_LOG_DIR` (`logs/questions`), `QUESTION_LOG_RETENTION_DAYS` (30),
`QUESTION_LOG_MAX_CHARS` (500), `QUESTION_LOG_ANSWERS=on|off` (off), `QUESTION_LOG_ANSWER_MAX_CHARS` (2000).
"""

import json
import logging
import queue
import re
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from futsal.agent.telemetry import (
    _REPLACE,  # mismos patrones de contacto que la telemetría (email, url, iban, dni, teléfono…)
)
from futsal.agent.trace import sanitize_text  # claves y secretos
from futsal.config.settings import env_value

log = logging.getLogger("futsal.questions")
QUEUE_SIZE = 1000
_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")
_STOP = None


def _int_env(key: str, default: int) -> int:
    try:
        value = int(env_value(key, str(default)))
    except ValueError:
        return default
    return value if value >= 1 else default


def _on(key: str) -> bool:
    return (env_value(key, "off") or "off").strip().lower() == "on"


def scrub(text: str) -> str:
    clean = sanitize_text(text)
    for label, pattern in _REPLACE:
        clean = pattern.sub(label, clean)
    return " ".join(clean.split())


class QuestionLog:
    def __init__(self, folder: Path, retention_days: int = 30, max_chars: int = 500, enabled: bool = True,
                 answers: bool = False, answer_max_chars: int = 2000) -> None:
        self.folder, self.retention_days, self.max_chars, self.enabled = folder, retention_days, max_chars, enabled
        self.answers, self.answer_max_chars = answers, answer_max_chars
        self.dropped = 0
        self._queue: queue.Queue[tuple[datetime, str, str | None] | None] = queue.Queue(QUEUE_SIZE)
        self._pruned: date | None = None
        self._thread: threading.Thread | None = None
        if enabled:
            self._thread = threading.Thread(target=self._writer, name="futsal-question-log", daemon=True)
            self._thread.start()

    @classmethod
    def from_env(cls) -> "QuestionLog":
        return cls(Path(env_value("QUESTION_LOG_DIR", "logs/questions") or "logs/questions"),
                   _int_env("QUESTION_LOG_RETENTION_DAYS", 30), _int_env("QUESTION_LOG_MAX_CHARS", 500),
                   _on("QUESTION_LOG"), _on("QUESTION_LOG_ANSWERS"), _int_env("QUESTION_LOG_ANSWER_MAX_CHARS", 2000))

    def submit(self, question: str, answer: str | None = None, received: datetime | None = None) -> None:
        """Encola sin esperar nunca. No lanza. `answer` solo se conserva con `answers` activado."""
        if not self.enabled:
            return
        kept = answer[: self.answer_max_chars] if self.answers and answer is not None else None
        try:
            self._queue.put_nowait((received or datetime.now(UTC), question[: self.max_chars], kept))
        except queue.Full:
            self.dropped += 1

    def close(self, timeout: float = 2.0) -> None:
        """Vacía lo pendiente (con límite de tiempo) y para el hilo."""
        if self._thread is None:
            return
        try:
            self._queue.put(_STOP, timeout=timeout)
        except queue.Full:
            pass
        self._thread.join(timeout)
        self._thread = None

    def _writer(self) -> None:
        while True:
            item = self._queue.get()
            batch = [item]
            while item is not _STOP:  # agrupa lo que ya esté en cola: una apertura de fichero por lote y día
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                batch.append(item)
            entries = [e for e in batch if e is not _STOP]
            if entries:
                try:
                    self._write(entries)
                except Exception as exc:  # noqa: BLE001 - el registro jamás afecta al chat
                    log.warning("preguntas no registradas: %d (%s)", len(entries), type(exc).__name__)
            if _STOP in batch:
                return

    def _write(self, entries: list[tuple[datetime, str, str | None]]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        by_day: dict[str, list[str]] = {}
        for ts, text, answer in entries:
            row: dict[str, str | None] = {"ts": ts.isoformat(timespec="seconds").replace("+00:00", "Z"), "question": scrub(text)}
            if self.answers:
                row["answer"] = None if answer is None else scrub(answer)
            line = json.dumps(row, ensure_ascii=False)
            by_day.setdefault(ts.date().isoformat(), []).append(line + "\n")
        for day, lines in by_day.items():
            with (self.folder / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write("".join(lines))
        self._prune(entries[-1][0].date())

    def _prune(self, today: date) -> None:
        """Como mucho una vez por día: borra los ficheros diarios más antiguos que la retención."""
        if self._pruned == today:
            return
        self._pruned = today
        limit = (today - timedelta(days=self.retention_days - 1)).isoformat()
        for path in self.folder.iterdir():
            m = _FILE.match(path.name)
            if m and m.group(1) < limit:
                path.unlink(missing_ok=True)
