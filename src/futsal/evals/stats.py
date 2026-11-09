"""Estadística de los informes y de la comparación: Wilson, percentiles y bootstrap pareado con semilla fija."""

import math
import random
from collections.abc import Sequence

Z95 = 1.959964
BOOTSTRAP_SEED = 20260101
BOOTSTRAP_ROUNDS = 2000


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Intervalo de Wilson de la proporción k/n (0, 1 si n = 0)."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, centre - half), 6), round(min(1.0, centre + half), 6)


def percentile(values: Sequence[float], q: float) -> float | None:
    """Percentil `q` (0-100) por interpolación lineal; None si no hay valores."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo), 3)


def mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def bootstrap_ci(diffs: Sequence[float], seed: int = BOOTSTRAP_SEED, rounds: int = BOOTSTRAP_ROUNDS,
                 alpha: float = 0.05) -> tuple[float, float] | None:
    """IC (1-alpha) de la media de las diferencias pareadas por caso, remuestreando casos con semilla fija."""
    n = len(diffs)
    if n == 0:
        return None
    rng = random.Random(seed)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(rounds))
    return round(means[int(rounds * alpha / 2)], 6), round(means[int(rounds * (1 - alpha / 2)) - 1], 6)
