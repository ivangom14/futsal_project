"""Limitador de ritmo con reloj falso (sin dormir de verdad)."""

import pytest

from futsal.ingestion.queue.ratelimit import RateLimiter


class FakeTime:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make(t: FakeTime, lo: float = 3, hi: float = 60) -> RateLimiter:
    return RateLimiter(lo, hi, clock=t.clock, sleep=t.sleep)


def test_respects_minimum_interval_per_host() -> None:
    t = FakeTime()
    rl = make(t)
    assert rl.wait("a") == 0 and t.sleeps == []  # la primera no espera
    t.now += 1
    assert rl.wait("a") == pytest.approx(2) and t.sleeps == [pytest.approx(2)]
    t.now += 10  # ya pasó el intervalo: no duerme
    assert rl.wait("a") == 0
    assert rl.wait("b") == 0  # otro host, contador propio


def test_penalize_doubles_up_to_cap_and_reward_recovers_gradually() -> None:
    t = FakeTime()
    rl = make(t)
    seen = []
    for _ in range(7):
        rl.penalize()
        seen.append(rl.interval)
    assert seen == [6, 12, 24, 48, 60, 60, 60]
    rl.wait("h")
    rl.wait("h")
    assert t.sleeps[-1] == 60  # la espera penalizada se aplica de verdad
    rewards = []
    for _ in range(6):
        rl.reward()
        rewards.append(rl.interval)
    assert rewards == [30, 15, 7.5, 3.75, 3, 3]  # baja poco a poco y nunca por debajo del mínimo


def test_invalid_bounds() -> None:
    with pytest.raises(ValueError):
        RateLimiter(10, 5)
