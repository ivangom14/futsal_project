"""Benchmark multimodelo sin BD ni red: registro de modelos, coste por llamada, presupuesto en USD y análisis comparativo."""

import json
from pathlib import Path
from typing import Any

import pytest

from futsal.agent.llm import AnthropicLLM, GeminiLLM, LLMError, Usage
from futsal.evals.bench import registry
from futsal.evals.bench.analysis import (
    CLASSES,
    classify,
    compare,
    model_stats,
    paired,
    pareto,
    turn_rows,
)
from futsal.evals.bench.registry import RegistryError, call_cost, parse_model
from futsal.evals.bench.runner import CostGuard, redact, suite_case_ids

ROOT = Path(__file__).parents[2]


def spec(**over: Any) -> registry.ModelSpec:
    raw: dict[str, Any] = {"id": "m1", "provider": "anthropic", "adapter": "anthropic", "model": "claude-haiku-5-5",
                           "credentials_env": "BENCH_TEST_KEY", "max_output_tokens": 1000,
                           "pricing": {"input": 1.0, "output": 10.0, "cached_input": 0.1, "source": "x", "checked_at": "2026-11-09"}}
    raw.update(over)
    return parse_model(raw)


# ----------------------------------------------------------------------------- registro
def test_repository_registry_loads_with_dated_prices_and_two_enabled_models() -> None:
    specs = registry.load_registry(root=ROOT)
    enabled = [s.id for s in specs.values() if s.enabled]
    assert enabled == ["gemini-flash-lite", "claude-haiku"]
    for s in specs.values():
        assert s.pricing.source and s.pricing.input is not None and s.pricing.output is not None
    assert specs["claude-haiku"].pricing.long_context and specs["claude-haiku"].pricing.long_context.above_input_tokens == 100000
    assert specs["gemini-flash-lite"].model == "gemini-3.5-flash-lite"  # el modelo actual de Futsito


@pytest.mark.parametrize(("over", "msg"), [
    ({"adapter": "ollama"}, "adaptador"),
    ({"params": {"reasoning_effort": "low"}}, "parámetros no admitidos"),
    ({"pricing": None}, "pricing"),
    ({"pricing": {"input": -1, "output": 1}}, "≥ 0"),
    ({"id": "Mal Id"}, "id"),
    ({"max_output_tokens": 0}, "max_output_tokens"),
])
def test_invalid_entries_are_rejected_with_a_clear_message(over: dict[str, Any], msg: str) -> None:
    with pytest.raises(RegistryError, match=msg):
        spec(**over)


def test_duplicate_ids_and_unknown_selection_are_rejected(tmp_path: Path) -> None:
    f = tmp_path / "m.yaml"
    entry = {"id": "a", "provider": "gemini", "adapter": "gemini", "model": "x", "credentials_env": "K", "pricing": {"input": 1, "output": 1}}
    f.write_text(json.dumps({"models": [entry, entry]}), encoding="utf-8")
    with pytest.raises(RegistryError, match="repetido"):
        registry.load_registry(f)
    f.write_text(json.dumps({"models": [entry | {"enabled": False}]}), encoding="utf-8")
    specs = registry.load_registry(f)
    with pytest.raises(RegistryError, match="no hay modelos activados"):
        registry.select(specs, None)
    assert [s.id for s in registry.select(specs, ["a"])] == ["a"]  # pedido explícitamente: vale aunque esté desactivado
    with pytest.raises(RegistryError, match="no configurados"):
        registry.select(specs, ["zzz"])


def test_unknown_prices_stay_unknown_and_new_model_needs_only_config() -> None:
    s = spec(id="nuevo", provider="gemini", adapter="gemini", model="gemini-x", pricing={"input": 0.2, "output": None})
    assert s.pricing.status == "unknown" and s.as_dict()["pricing"]["output"] is None


def test_build_llm_reads_the_key_at_build_time_and_never_exposes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    s = spec()
    monkeypatch.delenv("BENCH_TEST_KEY", raising=False)
    with pytest.raises(LLMError, match="BENCH_TEST_KEY no definida"):
        registry.build_llm(s)
    monkeypatch.setenv("BENCH_TEST_KEY", "sk-secreta-123456")
    llm = registry.build_llm(s)
    assert isinstance(llm, AnthropicLLM) and llm._prompt_cache is False and llm._max_tokens == 1000  # caché siempre desactivada
    assert "sk-secreta" not in json.dumps(s.as_dict())
    g = registry.build_llm(spec(adapter="gemini", provider="gemini", model="gemini-3.5-flash-lite", params={"thinking_level": "low"}))
    assert isinstance(g, GeminiLLM) and g._thinking_level == "low"
    assert redact("error con sk-secreta-123456 dentro", ["sk-secreta-123456"]) == "error con *** dentro"


# ----------------------------------------------------------------------------- coste por llamada
def test_anthropic_counts_cache_reads_apart_and_gemini_inside_input() -> None:
    a = call_cost(spec(), {"input": 1000, "cached": 500, "output": 100, "thinking": 0, "token_source": "provider"})
    assert a.basis == "provider" and a.usd == pytest.approx((1000 * 1.0 + 500 * 0.1 + 100 * 10) / 1e6)
    assert a.usd_no_cache == pytest.approx((1500 * 1.0 + 100 * 10) / 1e6)
    g = call_cost(spec(adapter="gemini", provider="gemini"), {"input": 1000, "cached": 500, "output": 100, "thinking": 50,
                                                             "token_source": "provider"})
    assert g.usd == pytest.approx((500 * 1.0 + 500 * 0.1 + 150 * 10) / 1e6)  # el razonamiento se factura como salida


def test_long_context_tier_estimated_tokens_and_unknown_rates() -> None:
    s = spec(pricing={"input": 1.0, "output": 10.0, "cached_input": 0.1, "long_context": {"above_input_tokens": 1000, "input": 5.0, "output": 20.0}})
    assert call_cost(s, {"input": 2000, "output": 10, "token_source": "provider"}).usd == pytest.approx((2000 * 5 + 10 * 20) / 1e6)
    assert call_cost(s, {"input": 2000, "cached": 10, "output": 10, "token_source": "provider"}).basis == "unavailable"  # caché larga sin tarifa
    assert call_cost(spec(), {"input": 10, "output": 1, "token_source": "estimated"}).basis == "estimated"
    assert call_cost(spec(), {"input": 10, "output": 1, "token_source": "unreported"}).usd is None
    no_cache_rate = spec(pricing={"input": 1.0, "output": 1.0})
    assert call_cost(no_cache_rate, {"input": 10, "cached": 5, "output": 1, "token_source": "provider"}).usd is None


# ----------------------------------------------------------------------------- presupuesto
def test_cost_guard_refuses_before_sending_and_accumulates_calculated_cost() -> None:
    s = spec()
    guard = CostGuard(budget_usd=0.02)
    guard.check(s)  # peor caso inicial: 8000 × 1 + 1000 × 10 = 0,018 $
    guard.add(s, Usage(10000, 500, 0, None, False, 0))
    assert guard.spent_usd == pytest.approx((10000 + 5000) / 1e6)
    with pytest.raises(LLMError, match="presupuesto de peticiones agotado"):
        guard.check(s)  # ahora el peor caso usa 1,5 × 10000 de entrada
    assert guard.refused == 1
    CostGuard(budget_usd=None).check(s)  # sin presupuesto (solo dry-run) no limita


def test_limit_takes_the_first_cases_interleaved_by_category() -> None:
    suite = json.loads((ROOT / "evals/bench/suites/bench-v1.json").read_text(encoding="utf-8"))
    first = suite_case_ids(suite, len(suite["categories"]))
    cats = {c["id"]: c["category"] for c in suite["cases"]}
    assert {cats[i] for i in first} == set(suite["categories"])  # --limit 10 cubre las 10 categorías
    assert 30 <= len(suite["cases"]) <= 40 and sum(c["turns"] > 1 for c in suite["cases"]) >= 6


# ----------------------------------------------------------------------------- análisis
def rec(case: str, verdict: str, *, turn: int = 0, rep: int = 0, calls: int = 1, tokens: tuple[int, int] = (1000, 100),
        latency: float = 1000.0, reason: str = "", source: str = "provider") -> dict[str, Any]:
    detail = [{"index": i, "input": tokens[0], "cached": 0, "output": tokens[1], "thinking": 0, "token_source": source,
               "retries": 0, "error": None} for i in range(calls)]
    return {"case_id": case, "turn": turn, "rep": rep, "question": f"¿{case}?", "answer": "respuesta", "verdict": verdict, "reason": reason,
            "tools_used": ["get_standings"], "grade": None, "llm_calls_detail": detail,
            "metrics": {"llm_calls": calls, "mcp_calls": calls, "retries": 0, "errors": 0, "input_tokens": tokens[0] * calls,
                        "cached_tokens": 0, "output_tokens": tokens[1] * calls, "thinking_tokens": 0, "token_source": source},
            "timing": {"latency_ms": latency, "llm_call_ms": [latency / max(calls, 1)] * calls, "llm_paced_ms": [0.0] * calls}}


CASES = {"a": {"category": "clasificacion", "complexity": "simple", "turns": 1},
         "b": {"category": "encadenadas", "complexity": "complex", "turns": 2},
         "c": {"category": "adversariales", "complexity": "complex", "turns": 1}}


def test_classification_covers_the_six_classes_plus_not_run() -> None:
    got = [classify(r) for r in (rec("a", "correct"), rec("a", "partial"), rec("a", "fail"), rec("a", "critical"),
                                 rec("a", "not_evaluable", reason="infra: 503"), rec("a", "not_evaluable", reason="falta capacidad"),
                                 rec("a", "review"), rec("a", "not_run"))]
    assert got == ["correcta", "parcial", "incorrecta", "alucinacion", "error_tecnico", "no_evaluable", "no_evaluable", "no_ejecutada"]
    assert set(got) == set(CLASSES)


def test_model_stats_costs_per_correct_conversation_and_projection() -> None:
    s = spec()
    res = {"records": [rec("a", "correct"), rec("b", "correct", turn=0), rec("b", "fail", turn=1), rec("c", "critical"),
                       rec("a", "not_run", calls=0)]}
    rows = turn_rows(res, s, CASES)
    st = model_stats(rows, s, None)
    per_q = (1000 * 1.0 + 100 * 10) / 1e6
    assert st["quality"]["accuracy"]["k"] == 2 and st["quality"]["accuracy"]["n"] == 4 and st["quality"]["hallucination"]["k"] == 1
    assert st["cost"]["total_usd"] == pytest.approx(4 * per_q) and st["cost"]["per_correct"] == pytest.approx(2 * per_q)
    assert st["cost"]["per_chained_conversation"]["mean"] == pytest.approx(2 * per_q)
    assert st["cost"]["projections"]["1000"]["usd"] == pytest.approx(1000 * per_q, rel=1e-3)
    assert st["chained_conversations"]["k"] == 0 and st["quality"]["small_sample"] is True
    assert st["cost"]["basis"] == {"provider": 4}


def test_unpriceable_questions_are_not_summed_as_zero() -> None:
    s = spec(pricing={"input": None, "output": None})
    st = model_stats(turn_rows({"records": [rec("a", "correct")]}, s, CASES), s, None)
    assert st["cost"]["total_usd"] == 0 and st["cost"]["questions_priced"] == 0 and st["cost"]["per_correct"] is None
    assert st["cost"]["complete"] is False and st["cost"]["per_question"]["mean"] is None


def test_paired_difference_pareto_and_conservative_recommendations() -> None:
    cheap, good = spec(id="cheap", pricing={"input": 0.1, "output": 1.0}), spec(id="good", pricing={"input": 5.0, "output": 50.0})
    cases = {f"k{i}": {"category": "clasificacion" if i % 2 else "goleadores", "complexity": "simple" if i < 20 else "complex", "turns": 1}
             for i in range(40)}
    good_recs = [rec(f"k{i}", "correct") for i in range(40)]
    cheap_recs = [rec(f"k{i}", "correct" if i < 20 or i % 2 else "fail", latency=500.0) for i in range(40)]  # falla en complejas
    rows = {"good": turn_rows({"records": good_recs}, good, cases), "cheap": turn_rows({"records": cheap_recs}, cheap, cases)}
    stats = {m: model_stats(rows[m], sp, None) for m, sp in (("good", good), ("cheap", cheap))}
    p = paired(rows["good"], rows["cheap"])
    assert p["n"] == 40 and p["diff"] == pytest.approx(0.25) and p["verdict"] == "A mejor"
    assert pareto(stats) == ["cheap", "good"]  # cada uno gana en algo: ninguno dominado
    c = compare(stats, rows)
    assert c["rank_quality"] == ["good", "cheap"] and c["rank_economic"][0] == "cheap"
    recs = c["recommendations"]
    assert recs["best_overall"]["model"] == "good" and recs["cheapest_acceptable"]["model"] == "good"  # el barato es peor (demostrado)
    assert recs["best_simple"]["model"] == "cheap" and recs["best_complex"]["model"] == "good"
    r = recs["router"]
    assert r["applicable"] and r["choice"]["simple"]["model"] == "cheap" and r["choice"]["complex"]["model"] == "good"
    assert r["saving_vs_reference"] and r["saving_vs_reference"] > 0.4 and r["worth_it"] is True


def test_small_equal_difference_is_reported_as_a_tie() -> None:
    a, b = spec(id="a"), spec(id="b", pricing={"input": 0.5, "output": 5.0})
    cases = {f"k{i}": {"category": "clasificacion", "complexity": "simple", "turns": 1} for i in range(10)}
    ra = [rec(f"k{i}", "correct" if i != 0 else "fail") for i in range(10)]
    rb = [rec(f"k{i}", "correct" if i != 1 else "fail") for i in range(10)]
    rows = {"a": turn_rows({"records": ra}, a, cases), "b": turn_rows({"records": rb}, b, cases)}
    stats = {"a": model_stats(rows["a"], a, None), "b": model_stats(rows["b"], b, None)}
    c = compare(stats, rows)
    assert c["pairs"]["a|b"]["verdict"] == "sin diferencia demostrada"
    best = c["recommendations"]["best_overall"]
    assert best["model"] == "b" and best["tied_with"] == ["a"]  # empate de calidad: decide la fiabilidad y después el coste


# ----------------------------------------------------------------------------- expectativa por arquitectura
def test_v2_cases_are_graded_with_their_v2_expectation_and_legacy_is_unchanged() -> None:
    from futsal.evals.runner import expected_for
    from futsal.evals.selector import ResolvedTurn

    turn = ResolvedTurn("¿Qué partidos hay el sábado?", "abstain", facts=[], forbid_any_score=True, tools_allowed=["list_matches"],
                        v2={"behavior": "answer", "facts": [{"kind": "number", "value": "7", "label": "partidos"}]})
    assert expected_for(turn, "legacy", frozenset({"matches_by_date"})) is turn
    v2 = expected_for(turn, "v2", frozenset({"matches_by_date"}))
    assert v2.behavior == "answer" and v2.facts[0]["value"] == "7" and v2.forbid_any_score is False and v2.tools_needed == []
    assert v2.tools_allowed == ["list_matches", "matches_by_date"]  # las herramientas v2 no cuentan como inesperadas
    plain = ResolvedTurn("¿Cuántos puntos tiene X?", "answer", facts=[{"kind": "number", "value": "9"}], tools_allowed=None)
    same = expected_for(plain, "v2", frozenset({"matches_by_date"}))
    assert same.behavior == "answer" and same.facts == plain.facts  # sin expectativa v2: la de siempre


def test_credentials_in_the_dotenv_file_are_found_like_in_the_rest_of_the_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCH_TEST_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    s = spec()
    assert s.has_credentials() is False
    (tmp_path / ".env").write_text("# claves\nBENCH_TEST_KEY=sk-solo-en-dotenv-123\n", encoding="utf-8")
    assert s.has_credentials() is True
    llm = registry.build_llm(s)
    assert isinstance(llm, AnthropicLLM) and llm._key == "sk-solo-en-dotenv-123"
    from futsal.evals.bench.runner import secrets_of
    assert secrets_of([s]) == ["sk-solo-en-dotenv-123"]  # también se ocultan en los mensajes de error
