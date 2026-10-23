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
    assert kinds.count("rule_cited") == 4 + 5 and kinds.count("no_evidence_rules") == 2 + 1
    assert all(c.required == {"search_rules"} for c in rule_cases())
    assert [c.plan is None for c in rule_cases() if c.facts[0].kind == "no_evidence_rules"] == [True, True, True]


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


FIFA_KEY, C27_KEY = "fifa-futsal-laws-2025-26", "rfef-circular-27-2025-26"


def _truth(indexed: bool | frozenset[str]) -> Truth:
    docs = frozenset({FIFA_KEY, C27_KEY}) if indexed is True else (frozenset() if indexed is False else indexed)
    return Truth(1, {}, {}, [], kb_docs=docs)


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
    assert len(cases) == 12 and all(c.facts is None and c.plan is None for c in cases)
    assert all("no está ingerido" in c.reason for c in cases)
    assert all(c.facts for c in build_cases(_truth(True)) if c.category.startswith("reglamento"))


def test_card_colour_in_rules_must_be_in_retrieved_text_not_banned():
    case = _case("regla_roja_mano")
    cards = [{"hits": [{"rule": 12, "pages": "90", "text": "Infracción por mano: Tarjeta roja si evita un gol."}]}]
    assert not any("tarjeta" in c for c in invention_claims(case, "Se sanciona con tarjeta roja (regla 12, página 90).",
                                                            _truth(True), cards))
    assert any("amarilla" in c for c in invention_claims(case, "Es tarjeta amarilla (regla 12).", _truth(True), cards))


def test_each_document_is_required_separately_missing_circular_is_not_evaluable() -> None:
    only_fifa = {c.id: c for c in build_cases(_truth(frozenset({FIFA_KEY}))) if c.category.startswith("reglamento")}
    assert all(c.facts for k, c in only_fifa.items() if k.startswith("regla_"))                 # el reglamento FIFA sigue evaluable
    circ = [c for k, c in only_fifa.items() if k.startswith("circular27_")]
    assert len(circ) == 6 and all(c.facts is None and C27_KEY in c.reason for c in circ)     # la circular no ingerida: no evaluable
    only_c27 = {c.id: c for c in build_cases(_truth(frozenset({C27_KEY}))) if c.category.startswith("reglamento")}
    assert only_c27["regla_duracion_mitad"].facts is None and only_c27["circular27_comunica"].facts
    assert only_c27["circular27_cesped_coloquial"].facts is None                                 # necesita ambos documentos


C27_HIT = {"rule": None, "pages": "1", "text": "la RFEF desea comunicar a todos sus afiliados el contenido de la Circular N.º 1941 de la FIFA"}


def test_circular27_cases_check_content_citation_and_source_separately() -> None:
    comunica = _case("circular27_comunica")
    fact = comunica.facts[0]
    assert evidence_ok(fact, [{"hits": [{"pages": "1", "text": C27_HIT["text"]}]}])             # circular sin regla: rule ausente = None
    assert not evidence_ok(fact, [{"hits": [{"pages": "2", "text": C27_HIT["text"]}]}])         # página equivocada
    assert rule_answer_issue(fact, "Comunica la Circular FIFA 1941 de la temporada 2025/2026 (página 1).") is None
    assert "página 1" in (rule_answer_issue(fact, "Comunica la Circular FIFA 1941 de la temporada 2025/2026.") or "")
    assert "cifra" in (rule_answer_issue(fact, "Comunica la circular de la FIFA (página 1).") or "")
    payloads = [{"hits": [C27_HIT]}]
    assert invention_claims(comunica, "Comunica la circular 1941 (temporada 2025/2026, página 1).", _truth(True), payloads) == []
    bad = invention_claims(comunica, "Es la circular 1942 de 2026, página 7.", _truth(True), payloads)
    assert any("1942" in c for c in bad) and any("página 7" in c for c in bad)


def test_circular27_dates_need_both_dates_and_each_page() -> None:
    case = _case("circular27_fechas_rfef_fifa")
    rfef, fifa = case.facts
    ok = "La RFEF la comunicó el 8 de septiembre de 2025 (página 1) y la FIFA la emitió el 5 de septiembre de 2025 (página 2)."
    assert rule_answer_issue(rfef, ok) is None and rule_answer_issue(fifa, ok) is None
    swapped = "La RFEF la comunicó el 5 de septiembre (página 1) y la FIFA el 8 de septiembre (página 2)."
    # limitación léxica conocida: con las fechas intercambiadas ambas figuran y el criterio no lo ve (revisión humana del informe)
    assert rule_answer_issue(rfef, swapped) is None and rule_answer_issue(fifa, swapped) is None
    only_one = "Se comunicó el 8 de septiembre de 2025 (página 1)."
    assert rule_answer_issue(rfef, only_one) is None and "dato correcto" in (rule_answer_issue(fifa, only_one) or "")


def test_circular27_without_evidence_rejects_attributing_sanctions_to_the_federation() -> None:
    case = _case("circular27_sin_evidencia_rffm")
    hits = [{"hits": [{"rule": None, "pages": "2", "text": "Circular FIFA n.º 1941 Reglas de Juego del Futsal 2025/2026"}]}]
    assert answer_ok(case.facts[0], "La Circular 27 no recoge sanciones de la RFFM; no consta ese dato.")
    claims = invention_claims(case, "La RFFM sanciona con dos partidos de suspensión y 300 euros de multa.", _truth(True), hits)
    assert any("importe" in c for c in claims) and any("300" in c for c in claims)
