"""Banco de comprensión conversacional: tamaño, umbrales por modo y garantía de que `legacy` no ha cambiado (ver `futsal.agent.understanding_eval`)."""

import json
from pathlib import Path

import pytest

from futsal.agent.understanding_cases import CASES
from futsal.agent.understanding_eval import evaluate, load_cases
from futsal.agent.understanding_holdout import HOLDOUT

FIXTURES = Path(__file__).parent / "fixtures" / "understanding"


def test_the_dataset_has_the_promised_size_and_shape() -> None:
    assert 150 <= len(CASES) + len(HOLDOUT) <= 400 and len(CASES) >= 150
    multi = [c for c in [*CASES, *HOLDOUT] if len(c["turns"]) > 1 or c.get("history")]
    assert len(multi) >= 40 and len({c["id"] for c in [*CASES, *HOLDOUT]}) == len(CASES) + len(HOLDOUT)
    assert any(t["q"] == "¿Cómo va la liga preferente?" and c.get("history") for c in CASES for t in c["turns"])  # el caso de la captura
    for c in load_cases("all"):
        for t in c["turns"]:
            assert t["q"] and (t.get("expect") is None or t["expect"]["action"] in ("act", "assume", "ask", "missing", "nodata", "refuse"))


@pytest.mark.parametrize("which", ["dev", "holdout"])
def test_new_modes_never_consult_a_wrong_competition_and_meet_the_quality_bar(which: str) -> None:
    res = evaluate(("deterministic", "llm"), which=which)["modes"]
    for mode, agg in res.items():
        assert agg["critical_wrong_competition"] == 0 and agg["refusals_wrong"] == 0 and agg["false_positives"] == 0, (mode, agg["failures"])
        assert agg["pass_rate"] >= 0.97 and agg["multi_turn_pass_rate"] >= 0.95 and agg["blocked_by_context"] == 0, (mode, agg["failures"])
    assert res["llm"]["pass_rate"] >= res["deterministic"]["pass_rate"] and res["deterministic"]["interpreter_calls_total"] == 0


def test_the_interpreter_is_conditional_not_indiscriminate() -> None:
    agg = evaluate(("llm",), which="all")["modes"]["llm"]
    assert 0 < agg["interpreter_calls_total"] < agg["turns"] * 0.25  # aporta en los casos que lo necesitan, no en cada pregunta


@pytest.mark.parametrize("profile,capable", [("strict", False), ("capable", True)])
def test_legacy_is_byte_for_byte_what_the_llm_saw_before_the_new_architecture(profile: str, capable: bool) -> None:
    stored = json.loads((FIXTURES / "legacy_digests_c80095a.json").read_text(encoding="utf-8"))["digests"][profile]
    rows = evaluate(("legacy",), which="all", capable=capable)["rows"]["legacy"]
    assert {f"{r['case']}#{r['turn']}": r["digest"] for r in rows} == stored
