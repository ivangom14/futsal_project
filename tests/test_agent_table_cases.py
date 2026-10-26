"""Casos del evaluador sobre las tablas oficiales (clasificación y goleadores): contratos de los criterios, sin LLM real."""
from futsal.agent.evaluate import Truth, answer_ok, build_cases, evidence_ok

STANDINGS = {1: [{"position": 1, "team": "C.D. UNO", "points": 3, "played": 1}, {"position": 2, "team": "F.S. DOS", "points": 1, "played": 1}],
             4: [{"position": 1, "team": "F.S. DOS", "points": 10, "played": 4}, {"position": 2, "team": "C.D. UNO", "points": 9, "played": 4}]}
SCORERS = [{"player": "PEREZ, ANA", "team": "C.D. UNO", "goals": 5}, {"player": "RUIZ, LUIS", "team": "F.S. DOS", "goals": 5},
           {"player": "SANZ, PAU", "team": "C.D. UNO", "goals": 2}]


def _cases(truth: Truth) -> dict[str, object]:
    return {c.id: c for c in build_cases(truth) if c.category == "tablas"}


def _truth(**kw: object) -> Truth:
    return Truth(1, {}, {}, [], **kw)  # type: ignore[arg-type]


def test_without_official_tables_every_case_is_not_evaluable_with_the_reason() -> None:
    cases = _cases(_truth())
    assert len(cases) == 5 and all(c.facts is None and c.reason for c in cases.values())  # type: ignore[attr-defined]
    assert "ingest-standings" in cases["tabla_clasificacion_lider"].reason  # type: ignore[attr-defined]


def test_leader_case_checks_team_points_and_round_from_the_official_table() -> None:
    case = _cases(_truth(official_standings=STANDINGS, official_scorers=SCORERS))["tabla_clasificacion_lider"]
    row, rnd = case.facts  # type: ignore[attr-defined]
    payload = [{"source": {"round": 4}, "standings": STANDINGS[4]}]
    assert evidence_ok(row, payload) and evidence_ok(rnd, payload)
    assert not evidence_ok(row, [{"source": {"round": 1}, "standings": STANDINGS[1]}])           # otra jornada: otro líder
    assert answer_ok(row, "Lidera F.S. DOS con 10 puntos.") and answer_ok(rnd, "Tras la jornada 4 lidera F.S. DOS con 10 puntos.")
    assert not answer_ok(row, "Lidera F.S. DOS con 9 puntos.") and not answer_ok(row, "Lidera C.D. UNO con 10 puntos.")
    assert case.required == {"get_standings"} and "get_standings" in case.allowed_tools
    # reconstruir con resultados/actas = herramienta no esperada (descubrir grupos o equipos sí se permite)
    assert not case.allowed_tools & {"list_matches", "get_match", "get_match_report", "list_match_events", "top_scorers"}


def test_past_round_case_asks_for_the_first_captured_round() -> None:
    case = _cases(_truth(official_standings=STANDINGS))["tabla_clasificacion_jornada_pasada"]
    assert "jornada 1" in case.question and case.facts[0].names == ("C.D. UNO",) and case.facts[1].number == 1  # type: ignore[index]
    one_round = _cases(_truth(official_standings={4: STANDINGS[4]}))["tabla_clasificacion_jornada_pasada"]
    assert one_round.facts is None and "--all-rounds" in one_round.reason


def test_scorer_case_requires_every_tied_top_scorer_and_the_exact_goals() -> None:
    case = _cases(_truth(official_standings=STANDINGS, official_scorers=SCORERS))["tabla_goleadores_maximo"]
    assert [f.names[0] for f in case.facts] == ["PEREZ, ANA", "RUIZ, LUIS"]                      # el empate se exige completo
    payload = [{"scorers": [{"player": "PEREZ, ANA", "goals": 5}, {"player": "RUIZ, LUIS", "goals": 5}, {"player": "SANZ, PAU", "goals": 2}]}]
    assert all(evidence_ok(f, payload) for f in case.facts)
    assert not all(evidence_ok(f, [{"scorers": [{"player": "PEREZ, ANA", "goals": 4}]}]) for f in case.facts)
    ok = "Hay empate a 5 goles entre Perez y Ruiz."
    assert all(answer_ok(f, ok) for f in case.facts) and not all(answer_ok(f, "El máximo goleador es Perez con 5 goles.") for f in case.facts)


def test_player_case_and_missing_player_case() -> None:
    cases = _cases(_truth(official_standings=STANDINGS, official_scorers=SCORERS))
    jug = cases["tabla_goleadores_jugador"]
    assert "SANZ, PAU" in jug.question and jug.facts[0].number == 2 and jug.plan is not None  # type: ignore[index]
    missing = cases["tabla_goleador_no_figura"]
    assert missing.facts[0].kind == "limitation" and missing.plan is None and missing.required == {"get_scorer_table"}  # type: ignore[index]
