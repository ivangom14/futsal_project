"""Contador de progreso de la descarga por catálogo: dónde va y cuánto queda. Solo presentación (sin red, sin estado persistente).

El total es una ESTIMACIÓN de peticiones HTTP (jornadas esperadas menos snapshots locales, más la semilla de cada grupo); los reintentos
y las jornadas vacías que devuelve la web pueden añadir peticiones, así que el tiempo restante es orientativo.
"""

import sys
import time
from collections.abc import Callable
from typing import Any, TextIO


def fmt_duration(seconds: float) -> str:
    s = max(int(round(seconds)), 0)
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


class Progress:
    def __init__(self, total_requests: int, groups: int, requests_made: Callable[[], int], *,
                 clock: Callable[[], float] = time.monotonic, out: TextIO | None = None,
                 live: bool | None = None, group_word: str = "grupo", item_word: str = "jornada") -> None:
        self.total, self.groups, self._requests, self._clock = max(total_requests, 0), groups, requests_made, clock
        self.out = out if out is not None else sys.stderr
        self.live = (hasattr(self.out, "isatty") and self.out.isatty()) if live is None else live  # \r solo en terminal
        self.start = clock()
        self.base = requests_made()
        self.group_index = 0
        self.label = ""
        self.group_rounds: tuple[int, int] = (0, 0)
        self.finished_groups = 0
        self._width = 0
        self.group_word, self.item_word = group_word, item_word

    # -- datos ------------------------------------------------------------------------------------------------------
    def done(self) -> int:
        return self._requests() - self.base

    def remaining_seconds(self) -> float | None:
        done = self.done()
        if done <= 0 or self.total <= 0:
            return None
        return max(self.total - done, 0) * (self._clock() - self.start) / done

    def line(self) -> str:
        done = self.done()
        pos, tot = self.group_rounds
        parts = [f"[{self.group_word} {self.group_index}/{self.groups}] {self.label}"]
        if tot:
            parts.append(f"{self.item_word} {pos}/{tot}")
        if self.total:
            parts.append(f"peticiones {done}/~{self.total} ({min(done * 100 // self.total, 100)} %)")
        else:
            parts.append(f"peticiones {done}")
        parts.append(f"transcurrido {fmt_duration(self._clock() - self.start)}")
        eta = self.remaining_seconds()
        parts.append("restante ~" + fmt_duration(eta) if eta is not None else "restante n/d")
        return " | ".join(parts)

    # -- eventos ---------------------------------------------------------------------------------------------------
    def start_group(self, index: int, competition_id: str, group_id: str, name: str | None) -> None:
        self.group_index, self.label, self.group_rounds = index, f"{competition_id}/{group_id} {name or ''}".strip(), (0, 0)
        self._emit(self.line())

    def begin(self, label: str, total_requests: int | None = None) -> None:
        """Para lotes sin grupos (p. ej. actas): fija la etiqueta y, si se conoce, el total previsto de peticiones."""
        self.group_index, self.label, self.group_rounds = 1, label, (0, 0)
        if total_requests is not None:
            self.total = max(total_requests, 0)
        self._emit(self.line())

    def on_round(self, position: int, total: int, from_cache: bool, failed: bool) -> None:
        self.group_rounds = (position, total)
        if self.live or position == total:  # sin terminal (redirigido) solo se escribe al terminar cada grupo
            self._emit(self.line())

    def end_group(self, result: dict[str, Any]) -> None:
        self.finished_groups += 1
        if self.live:
            self._emit(self.line(), final=True)
        elif result.get("status") in ("no_rounds", "failed"):
            self._emit(self.line(), final=True)

    def finish(self) -> None:
        if self.live and self._width:
            self.out.write("\r" + " " * self._width + "\r")
            self.out.flush()
        self.out.write(f"Descarga terminada en {fmt_duration(self._clock() - self.start)}: "
                       f"{self.done()} peticiones (estimadas ~{self.total}).\n")
        self.out.flush()

    def _emit(self, text: str, final: bool = False) -> None:
        if self.live:
            pad = " " * max(self._width - len(text), 0)
            self.out.write("\r" + text + pad)
            self._width = max(self._width, len(text))
            if final:
                self.out.write("\n")
                self._width = 0
        else:
            self.out.write(text + "\n")
        self.out.flush()
