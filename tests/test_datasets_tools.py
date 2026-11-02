"""Fase 2A: herramientas de datasets, huellas y guardia del Holdout B. Sin red, sin LLM."""

import json
from pathlib import Path
from typing import Any

import pytest

from futsal.agent import datasets as D

FIX = Path(__file__).parent / "fixtures" / "router"
B_LOCAL = Path(__file__).parent.parent / "data" / "holdout" / "holdout_b.json"


def case(cid: str = "x1", q: str = "¿Quién lidera la clasificación del grupo 3?", **over: Any) -> dict[str, Any]:
    base = {"id": cid, "question": q, "source": "blind", "category": "t", "intent": "standings", "needed_tools": ["get_standings"], "plausible_tools": ["get_standings", "list_groups"],
            "prefetch_expected": "abstain", "prefetch_entity": None, "abstain_reason": "no_team", "entity_ambiguity": "none", "scope": "resolved", "followup": False,
            "prev_questions": [], "focus": {}, "tier0_eligible": True, "season": "current"}
    return {**base, **over}


def test_validation_catches_inconsistent_labels() -> None:
    bad = {"cases": [case("a"), case("a", "otra pregunta"), case("b", "x", needed_tools=["inventada"]), case("c", "y", needed_tools=["find_teams"], plausible_tools=["get_standings"]),
                     case("d", "z", prefetch_expected="fire"), case("e", "w", followup=True), case("f", "v", season="historical"),
                     case("g", "¿quién LIDERA la clasificación del grupo 3?"), case("h", "q", source="inventado")]}
    errors = "\n".join(D.validate(bad))
    for needle in ("a: id repetido", "b: needed_tools con herramientas desconocidas", "c: needed_tools debe ser subconjunto", "d: «fire» exige", "e: un seguimiento",
                   "f: una consulta histórica", "g: pregunta repetida", "h: source"):
        assert needle in errors, needle
    assert D.validate({"cases": [case("ok")]}) == []


def test_overlaps_detect_exact_and_near_duplicates_only() -> None:
    a = {"cases": [case("a1", "¿Cuánto dura cada mitad del partido?"), case("a2", "Dime el goleador del equipo rojo")]}
    b = {"cases": [case("b1", "¿cuánto dura cada mitad del partido?"), case("b2", "¿Cuánto dura cada mitad del partido hoy?"), case("b3", "Resultados de la jornada 3")]}
    assert {(x, y) for x, y, _ in D.overlaps(a, b)} == {("a1", "b1"), ("a1", "b2")}


def test_seal_detects_any_later_change(tmp_path: Path) -> None:
    path, manifest = tmp_path / "h.json", tmp_path / "h.manifest.json"
    path.write_text(json.dumps({"cases": [case("a")]}), encoding="utf-8")
    m = D.seal(path, manifest, "nota")
    assert m["n"] == 1 and D.verify(path, manifest)[0] is True
    path.write_text(json.dumps({"cases": [case("a", "¿Quién lidera la clasificación del grupo 4?")]}), encoding="utf-8")
    ok, msg = D.verify(path, manifest)
    assert ok is False and "≠" in msg
    path.write_text(json.dumps({"cases": [case("a", prefetch_expected="fire")]}), encoding="utf-8")
    with pytest.raises(ValueError):
        D.seal(path, manifest)  # un dataset inválido no se sella


def test_adding_own_questions_validates_rejects_clashes_and_records_history(tmp_path: Path) -> None:
    path, manifest, other = tmp_path / "h.json", tmp_path / "h.manifest.json", tmp_path / "otro.json"
    path.write_text(json.dumps({"cases": [case("a")]}), encoding="utf-8")
    other.write_text(json.dumps({"cases": [case("z", "Pregunta ya usada para ajustar el router")]}), encoding="utf-8")
    D.seal(path, manifest)
    m = D.add_cases(path, [case("n1", "¿Qué equipos hay en el grupo 2?", source="real_anonymized")], manifest, [other], "preguntas propias")
    assert m["n"] == 2 and m["history"][-1]["added"] == 1 and m["history"][-1]["note"] == "preguntas propias" and D.verify(path, manifest)[0]
    with pytest.raises(ValueError):
        D.add_cases(path, [case("n2", "Pregunta ya usada para ajustar el router")], manifest, [other])  # solapa con el ajuste
    with pytest.raises(ValueError):
        D.add_cases(path, [case("n1", "otra")], manifest)  # id repetido
    assert D.load(path)["cases"][-1]["id"] == "n1" and len(D.load(path)["cases"]) == 2


def test_holdout_b_needs_an_explicit_acknowledgement_and_every_read_is_logged(tmp_path: Path) -> None:
    path, log = tmp_path / "b.json", tmp_path / "b_access.log"
    path.write_text(json.dumps({"cases": [case("a")]}), encoding="utf-8")
    with pytest.raises(PermissionError, match="evaluación final"):
        D.load_holdout_b(path, acknowledge=False, purpose="ajuste", log_path=log)
    assert not log.exists()
    assert D.load_holdout_b(path, acknowledge=True, purpose="final", log_path=log)["cases"][0]["id"] == "a"
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert entry["purpose"] == "final" and entry["sha256"] == D.sha256_file(path)


def test_holdout_a_is_sealed_and_large_enough() -> None:
    ok, msg = D.verify(FIX / "holdout_a.json", FIX / "holdout_a.manifest.json")
    assert ok, f"Holdout A modificado tras sellarse: {msg}"
    doc = D.load(FIX / "holdout_a.json")
    s = D.stats(doc)
    assert D.validate(doc) == [] and s["n"] >= 300 and s["prefetch_expected"]["abstain"] >= 555 and s["prefetch_expected"]["fire"] >= 368
    assert all(c["needed_tools"] is not None and set(c["needed_tools"]) <= set(c["plausible_tools"]) for c in doc["cases"])  # etiquetas de herramientas, no solo intención


def test_holdout_a_shares_no_question_with_the_tuning_sets() -> None:
    a = D.load(FIX / "holdout_a.json")
    for name in ("design_set.json", "validation.json"):
        assert D.overlaps(a, D.load(FIX / name)) == [], name
    assert D.overlaps(a, D.load(FIX.parent / "entities" / "entities_dev.json")) == []


def test_holdout_b_manifest_is_versioned_but_never_its_content() -> None:
    manifest = json.loads((FIX / "holdout_b.manifest.json").read_text(encoding="utf-8"))
    assert manifest["n"] >= 300 and manifest["counts"]["prefetch_expected"]["abstain"] >= 555 and "cases" not in manifest and "question" not in json.dumps(manifest).lower().replace("preguntas", "")
    assert not list(FIX.glob("holdout_b*.json")) == [] and not (FIX / "holdout_b.json").exists()  # solo el manifiesto está en el repositorio
    if B_LOCAL.exists():  # en tu máquina, con el fichero de data/holdout/: la huella debe coincidir
        assert D.verify(B_LOCAL, FIX / "holdout_b.manifest.json")[0]
