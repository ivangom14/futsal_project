"""Estadística mínima para decir cuánto se puede afirmar con una muestra (Fase 2A). Intervalos exactos de Clopper-Pearson, sin dependencias.

Regla del proyecto: un objetivo («falsos positivos ≤ 1 %», «precisión ≥ 95 %») solo se da por cumplido si la COTA del intervalo lo respalda con ese tamaño de muestra;
un porcentaje puntual sobre pocos casos no demuestra nada.
"""

import math


def _cdf(k: int, n: int, p: float) -> float:
    """P(X ≤ k) para X ~ Binomial(n, p), con logaritmos (estable para n grandes)."""
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    total = 0.0
    for i in range(0, k + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * math.log(p) + (n - i) * math.log1p(-p))
    return min(total, 1.0)


def _bisect(f, lo: float, hi: float) -> float:  # type: ignore[no-untyped-def]
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) else (lo, mid)
    return (lo + hi) / 2


def cp_interval(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Intervalo exacto BILATERAL de la proporción k/n. (0, 1) si n = 0."""
    if n <= 0:
        return 0.0, 1.0
    a = (1 - confidence) / 2
    lower = 0.0 if k == 0 else _bisect(lambda p: 1 - _cdf(k - 1, n, p) < a, 0.0, 1.0)  # P(X ≥ k | p) crece con p
    upper = 1.0 if k == n else _bisect(lambda p: _cdf(k, n, p) > a, 0.0, 1.0)  # P(X ≤ k | p) decrece con p
    return round(lower, 6), round(upper, 6)


def min_n_for_upper_bound(max_rate: float, failures: int = 0, confidence: float = 0.95) -> int:
    """Muestra mínima para que, con `failures` fallos, el LÍMITE SUPERIOR bilateral del intervalo no supere `max_rate`."""
    n = max(failures, 1)
    while cp_interval(failures, n, confidence)[1] > max_rate:
        n += 1
    return n


def min_n_for_lower_bound(min_rate: float, failures: int = 0, confidence: float = 0.95) -> int:
    """Muestra mínima para que, con `failures` fallos, el LÍMITE INFERIOR bilateral de la tasa de aciertos no baje de `min_rate`."""
    n = failures + 1
    while cp_interval(n - failures, n, confidence)[0] < min_rate:
        n += 1
    return n


def verdict_upper(k: int, n: int, max_rate: float, confidence: float = 0.95) -> str:
    """«cumple» solo si el límite superior respalda el objetivo; «no concluyente» si el valor puntual lo cumple pero la muestra no alcanza; «no cumple» si ni el puntual."""
    if n <= 0:
        return "no concluyente"
    if cp_interval(k, n, confidence)[1] <= max_rate:
        return "cumple"
    return "no concluyente" if k / n <= max_rate else "no cumple"
