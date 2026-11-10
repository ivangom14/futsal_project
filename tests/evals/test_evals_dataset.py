"""Batería futsito-v1: cantidades por estrato (§4), holdout sellado, smoke40, selector determinista y lector YAML."""

import json
from pathlib import Path
from typing import Any

import pytest

from futsal.agent.audit import letter  # noqa: F401
from futsal.agent.rule_cases import rule_cases
from futsal.evals.datasets import (
    counts,
    dataset_dir,
    load_cases,
    smoke_ids,
    suite_cases,
    verify_seals,
)
from futsal.evals.model import Case, CaseError, parse_case
from futsal.evals.selector import Unresolvable, lookup, render, resolve_case

ROOT = Path(__file__).parents[2]
CASES_PER_STRATUM = {"S1": 30, "S2": 20, "S3": 15, "S4": 15, "S5": 15, "S6": 15, "S7": 15, "S8": 15, "S9": 20}


def original(cases: list[Case]) -> list[Case]:
    """Los 160 casos de S1-S9 de la batería cerrada (sin el estrato S10 ni los ficheros `*_extra`)."""
    return [c for c in cases if c.stratum != "S10" and "_extra" not in c.source_file]


def test_dataset_has_the_designed_size_and_split() -> None:
    allc = original(load_cases(ROOT, "all"))
    assert len(allc) == 160 and {s: v["cases"] for s, v in counts(allc).items()} == CASES_PER_STRATUM
    assert sum(len(c.turns) for c in allc) == 190  # ≈ 190 turnos
    convs = [c for c in allc if c.stratum == "S3"]
    assert all(len(c.turns) >= 2 for c in convs) and sum(len(c.turns) for c in convs) == 45  # 15 conversaciones, 45 turnos
    hold = [c for c in allc if c.holdout]
    assert len(hold) == 48 and len(hold) / len(allc) == 0.3  # 30 % holdout
    assert {s: v["holdout"] for s, v in counts(allc).items()} == {"S1": 9, "S2": 6, "S3": 4, "S4": 4, "S5": 5, "S6": 5, "S7": 4, "S8": 5, "S9": 6}
    assert len(original(load_cases(ROOT, "dev"))) == 112 and len(original(load_cases(ROOT, "holdout"))) == 48
    assert len({c.id for c in allc}) == 160
    assert not [c.id for c in allc if c.capability == "v2" and c.stratum != "S7"]


def test_dataset_covers_confusions_missing_data_and_adversarial_cases() -> None:
    allc = load_cases(ROOT, "all")

    def uses(key: str, stratum: str | None = None) -> int:
        return sum(any(key in spec for spec in c.select.values()) for c in allc if stratum in (None, c.stratum))

    assert uses("homonym_groups") >= 6 and uses("same_competition") >= 3  # (el snapshot real no tiene homónimos dentro de una competición: los dev ya no lo exigen) equipos homónimos (grupos / competiciones)
    assert uses("diff_category") >= 6  # categorías (senior / juvenil / cadete / femenino)
    assert uses("diff_season") >= 3  # temporadas
    assert sum(spec.get("kind") in ("club", "club_multi") for c in allc for spec in c.select.values()) >= 5  # clubes con equipos A y B
    assert uses("category") >= 3 and uses("competition_groups") >= 2  # competición/categoría con varios grupos
    assert sum(any(spec.get("kind") in ("season_absent", "fictional_player") for spec in c.select.values()) for c in allc) >= 4
    assert sum(any(spec.get("kind") in ("fictional_team", "fictional_competition") for spec in c.select.values()) for c in allc) >= 4
    behaviors = [t.expect["behavior"] for c in allc for t in c.turns]
    assert behaviors.count("clarify") >= 12 and behaviors.count("abstain") >= 25 and behaviors.count("offtopic") >= 3
    assert sum("forbid_scores" in t.expect or "forbid_phrases" in t.expect for c in allc for t in c.turns) >= 6  # premisas falsas / presión


def test_holdout_is_sealed_and_default_split_never_reads_it() -> None:
    assert verify_seals(ROOT) == []
    seals = json.loads((dataset_dir(ROOT) / "SEALS.json").read_text(encoding="utf-8"))
    assert sum(f["cases"] for f in seals["files"].values()) == 48 + 18 and all(len(f["sha256"]) == 64 for f in seals["files"].values())
    assert all(c.holdout for c in load_cases(ROOT, "holdout")) and not any(c.holdout for c in load_cases(ROOT, "dev"))


def test_smoke40_is_a_fixed_dev_subset_covering_every_stratum() -> None:
    ids = smoke_ids(ROOT)
    cases = suite_cases("smoke40", "dev", ROOT)
    assert len(ids) == len(set(ids)) == 40 and [c.id for c in cases] == ids and not any(c.holdout for c in cases)  # fijo en 40 casos de S1-S9: es la referencia de coste de A5; S10 se evalúa con su propio estrato
    assert {c.stratum for c in cases} == set(CASES_PER_STRATUM) - {"S10"}
    assert any(len(c.turns) > 1 for c in cases if c.stratum == "S3")


def test_rule_cases_migrated_unchanged() -> None:
    by_id = {c.id: c for c in rule_cases()}
    migrated = [(c, t) for c in load_cases(ROOT, "all") for t in c.turns if t.expect.get("rule_case")]
    assert len(migrated) == 15
    for _c, t in migrated:
        assert t.user == by_id[t.expect["rule_case"]].question  # la pregunta es la actual, sin reescribir


def test_case_validation_rejects_bad_cases() -> None:
    ok = {"id": "x", "stratum": "S1", "holdout": False, "requires": [], "capability": "v1", "select": {},
          "turns": [{"user": "hola", "expect": {"behavior": "answer"}}]}
    assert isinstance(parse_case(ok), Case)
    for patch, why in (({"stratum": "S0"}, "estrato"), ({"turns": [{"user": "q", "expect": {"behavior": "x"}}]}, "behavior"),
                       ({"turns": [{"user": "q", "expect": {"behavior": "answer", "nope": 1}}]}, "desconocidas"),
                       ({"stratum": "S3"}, "conversaciones"), ({"select": {"a": {"kind": "planeta"}}}, "kind")):
        with pytest.raises(CaseError, match=why):
            parse_case({**ok, **patch})


# ----------------------------------------------------------------------------- selector y plantillas
def test_templates_resolve_paths_lists_and_pipes() -> None:
    b: dict[str, Any] = {"t": {"name": "C.D. DOSA", "n": 3, "items": [{"g": "A"}, {"g": "B"}], "gone": None}}
    assert render("{t.n}", b) == 3 and render("Equipo {t.name|lower} ({t.items[*].g})", b) == "Equipo c.d. dosa (A, B)"
    assert render(["{t.items[*].g}", "x"], b) == ["A", "B", "x"] and lookup("t.items[1].g", b) == "B"
    assert render("{t.name|unaccent|nopunct}", {"t": {"name": "C.D. Dósa"}}) == "CD Dosa"
    for bad in ("{t.missing}", "{t.gone}", "{t.items[5].g}", "{x.y}"):
        with pytest.raises(Unresolvable):
            render(bad, b)


def test_selector_is_deterministic_and_seeded_by_the_snapshot_fingerprint(synthetic: Any) -> None:
    from world import build_world

    w1, w2 = build_world(synthetic), build_world(synthetic)
    assert {k: v.to_dict() if False else (v.status, v.reason, [t.to_dict() for t in v.turns], v.bindings) for k, v in w1.resolved.items()} == \
           {k: (v.status, v.reason, [t.to_dict() for t in v.turns], v.bindings) for k, v in w2.resolved.items()}
    case = next(c for c in load_cases(synthetic.root, "dev") if c.id == "s1_puntos_coloquial")
    picks = set()
    for seed in ("a", "b", "c", "d", "e", "f"):
        w1.catalog.seed = seed
        picks.add(resolve_case(case, w1.catalog).turns[0].user)
    assert len(picks) > 1  # otra semilla elige otra entidad
    w1.catalog.seed = synthetic.manifest["fingerprint"]["sha256"]
    assert resolve_case(case, w1.catalog).turns[0].user == w2.resolved[case.id].turns[0].user


def test_missing_capability_or_entity_gives_not_evaluable_with_reason(synthetic: Any) -> None:
    from world import build_world

    w = build_world(synthetic)
    by_reason = {r.case_id: r.reason for r in w.resolved.values() if r.status != "ok"}
    assert any("capacidad ausente en el snapshot" in why for why in by_reason.values())
    assert any("ninguna entidad cumple select" in why for why in by_reason.values())
    never = parse_case({"id": "z", "stratum": "S1", "holdout": False, "requires": ["standings"], "capability": "v1",
                        "select": {"team": {"kind": "team", "with": ["standings"], "homonym_groups": ">=9"}},
                        "turns": [{"user": "¿{team.name}?", "expect": {"behavior": "answer"}}]})
    r = resolve_case(never, w.catalog)
    assert r.status == "not_evaluable" and "ninguna entidad cumple select.team" in r.reason
    needs_kb = parse_case({"id": "k", "stratum": "S9", "holdout": False, "requires": ["kb:otro-documento"], "capability": "v1", "select": {},
                           "turns": [{"user": "q", "expect": {"behavior": "answer"}}]})
    assert "capacidad ausente" in resolve_case(needs_kb, w.catalog).reason


def test_truth_comes_only_from_named_sql_files() -> None:
    from futsal.evals.catalog import TRUTH_DIR, sql_text

    names = {p.stem for p in TRUTH_DIR.glob("*.sql")}
    assert {"groups", "teams", "matches", "standings_latest", "scorers_latest", "report_goals", "kb_docs", "players"} <= names
    for n in names:
        assert sql_text(n).lstrip().lower().startswith(("--", "select", "with"))
