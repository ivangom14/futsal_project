"""Limitador de ritmo por host: un solo hilo, intervalo mínimo entre peticiones y espera adaptativa."""

import time
from collections.abc import Callable


class RateLimiter:
    """`wait(host)` bloquea (vía `sleep`) hasta que haya pasado el intervalo vigente desde la última petición a ese host.

    `penalize()` duplica el intervalo hasta `max_backoff_s`; `reward()` lo reduce a la mitad hasta `min_interval_s`.
    """

    def __init__(self, min_interval_s: float = 3.0, max_backoff_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        if min_interval_s < 0 or max_backoff_s < min_interval_s:
            raise ValueError("se exige 0 <= min_interval_s <= max_backoff_s")
        self.min_interval_s, self.max_backoff_s = min_interval_s, max_backoff_s
        self._clock, self._sleep = clock, sleep
        self._interval = min_interval_s
        self._last: dict[str, float] = {}

    @property
    def interval(self) -> float:
        return self._interval

    def wait(self, host: str) -> float:
        """Espera lo necesario y marca la petición. Devuelve los segundos dormidos."""
        slept = 0.0
        last = self._last.get(host)
        if last is not None:
            remaining = self._interval - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
                slept = remaining
        self._last[host] = self._clock()
        return slept

    def penalize(self) -> None:
        self._interval = min(self.max_backoff_s, max(self._interval, 1.0) * 2)

    def reward(self) -> None:
        self._interval = max(self.min_interval_s, self._interval / 2)
