"""Fase 2A: estadística de muestras. Valores de referencia calculados con el método exacto de Clopper-Pearson."""

import pytest

from futsal.agent.stats import cp_interval, min_n_for_lower_bound, min_n_for_upper_bound, verdict_upper


def test_clopper_pearson_known_values() -> None:
    assert cp_interval(0, 368) == (0.0, pytest.approx(0.009974, abs=2e-6))
    lo, hi = cp_interval(5, 100)
    assert lo == pytest.approx(0.016432, abs=2e-6) and hi == pytest.approx(0.112835, abs=2e-6)
    assert cp_interval(0, 0) == (0.0, 1.0) and cp_interval(10, 10)[1] == 1.0 and cp_interval(10, 10)[0] < 1.0


def test_sample_sizes_to_sustain_the_targets() -> None:
    assert [min_n_for_upper_bound(0.01, k) for k in (0, 1, 2)] == [368, 555, 720]  # falsos positivos ≤ 1 %, IC bilateral del 95 %
    assert [min_n_for_lower_bound(0.95, k) for k in (0, 1, 2)] == [72, 110, 142]  # aciertos ≥ 95 %


def test_a_target_is_met_only_when_the_upper_bound_supports_it() -> None:
    assert verdict_upper(0, 300, 0.01) == "no concluyente"  # 0 fallos, pero la muestra no basta para sostener el 1 %
    assert verdict_upper(0, 368, 0.01) == "cumple"
    assert verdict_upper(1, 368, 0.01) == "no concluyente"  # 0,27 % observado, límite superior > 1 %
    assert verdict_upper(5, 400, 0.01) == "no cumple" and verdict_upper(0, 0, 0.01) == "no concluyente"
