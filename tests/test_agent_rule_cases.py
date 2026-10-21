from futsal.agent.criteria import unsupported_digits, unsupported_rule_citations
from futsal.agent.evaluate import answer_ok, evidence_ok
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
