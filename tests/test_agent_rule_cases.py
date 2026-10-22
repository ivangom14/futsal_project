from futsal.agent.criteria import (
    invented_quantities,
    unsupported_digits,
    unsupported_rule_citations,
)
from futsal.agent.evaluate import (
    Truth,
    answer_ok,
    build_cases,
    evidence_ok,
    invention_claims,
    rule_answer_issue,
)
from futsal.agent.rule_cases import rule_cases

HIT = {"rule": 7, "pages": "60", "text": "El partido se desarrollará en dos mitades iguales de 20 minutos de juego efectivo."}
PAYLOADS = [{"hits": [HIT]}]


def _case(case_id: str):
    return next(c for c in rule_cases() if c.id == case_id)


def test_cases_cover_answerable_and_no_evidence():
    kinds = [c.facts[0].kind for c in rule_cases()]
    assert kinds.count("rule_cited") == 4 and kinds.count("no_evidence_rules") == 2
    assert all(c.required == {"search_rules"} for c in rule_cases())
    assert [c.plan is None for c in rule_cases() if c.facts[0].kind == "no_evidence_rules"] == [True, True]


def test_evidence_requires_expected_rule_page_and_text():
    fact = _case("regla_duracion_mitad").facts[0]
    assert evidence_ok(fact, PAYLOADS)
    assert not evidence_ok(fact, [{"hits": [{**HIT, "rule": 8}]}])
    assert not evidence_ok(fact, [{"hits": [{**HIT, "pages": "61"}]}])
    assert not evidence_ok(fact, [{"hits": [{**HIT, "text": "otro texto"}]}])
    assert not evidence_ok(fact, [{"hits": []}])


def test_answer_needs_figure_and_citation():
    fact = _case("regla_duracion_mitad").facts[0]
    assert answer_ok(fact, "Cada mitad dura 20 minutos (Regla 7, página 60).")
    assert not answer_ok(fact, "Cada mitad dura 20 minutos.")
    assert not answer_ok(fact, "Cada mitad dura 25 minutos (Regla 7).")
    assert answer_ok(_case("regla_sexta_falta").facts[0], "Desde la sexta falta acumulada (regla 13).")


def test_no_evidence_answer_must_admit_it():
    fact = _case("regla_sin_evidencia_salario").facts[0]
    assert answer_ok(fact, "El reglamento no recoge ese dato.")
    assert not answer_ok(fact, "Cobra unos 60 euros por partido.")


def test_unsupported_citations_and_digits():
    assert unsupported_rule_citations("Regla 7, página 60.", PAYLOADS) == []
    assert unsupported_rule_citations("Regla 9 y página 12.", PAYLOADS) == ["Regla 9", "página 12"]
    assert unsupported_digits("Dura 20 minutos (regla 7, p. 60).", PAYLOADS) == []
    assert unsupported_digits("Cobra 60 euros.", PAYLOADS, "¿Cuánto cobra?") == ["60"]


def _truth(indexed: bool) -> Truth:
    return Truth(1, {}, {}, [], rules_indexed=indexed)


def test_no_evidence_flags_invented_amounts_and_sanctions_but_accepts_admission():
    case = _case("regla_sin_evidencia_sancion_rffm")
    tangential = [{"hits": [{"rule": 12, "pages": "90", "text": "Expulsión: 2 minutos de exclusión."}]}]
    ok = "El reglamento de la FIFA no recoge las sanciones de la RFFM; no consta ese dato."
    assert invention_claims(case, ok, _truth(True), tangential) == []
    bad = "Corresponden dos partidos de sanción, según la regla 12."
    claims = invention_claims(case, bad, _truth(True), tangential)
    assert any("dos partidos" in c for c in claims)
    assert any("2" in c or "cifra" in c for c in invention_claims(case, "Son 2 partidos.", _truth(True), tangential))
    assert any("euros" in c for c in invention_claims(_case("regla_sin_evidencia_salario"), "Cobra unos 60 euros.",
                                                      _truth(True), []))
    assert invented_quantities("No consta ese dato.") == []


def test_rule_failures_are_told_apart():
    fact = _case("regla_duracion_mitad").facts[0]
    assert rule_answer_issue(fact, "Cada mitad dura 20 minutos (regla 7).") is None
    assert "cifra" in rule_answer_issue(fact, "Dura media hora (regla 7).")
    assert "no cita" in rule_answer_issue(fact, "Cada mitad dura 20 minutos.")
    assert not evidence_ok(fact, [{"hits": [{**HIT, "rule": 8}]}])  # recuperación
    case = _case("regla_duracion_mitad")
    assert any("cita" in c for c in invention_claims(case, "20 minutos, regla 9.", _truth(True), PAYLOADS))  # cita inventada


def test_missing_document_is_not_evaluable_not_a_pass():
    cases = [c for c in build_cases(_truth(False)) if c.category.startswith("reglamento")]
    assert len(cases) == 6 and all(c.facts is None and c.plan is None for c in cases)
    assert all("no está ingerido" in c.reason for c in cases)
    assert all(c.facts for c in build_cases(_truth(True)) if c.category.startswith("reglamento"))
