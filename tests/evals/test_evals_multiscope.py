"""Estrato S10 (consultas amplias / multiámbito) y casuísticas adicionales de S1-S9: carga, sellado, selector y verdad SQL."""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from world import World, build_world

from futsal.evals.catalog import TRUTH_DIR, Catalog, base_name, named_query, tokens_key
from futsal.evals.datasets import counts, dataset_dir, load_cases, verify_seals
from futsal.evals.model import SELECT_KINDS, Case
from futsal.evals.plan import build_plan
from futsal.evals.selector import PLACEHOLDER, referenced

ROOT = Path(__file__).parents[2]
# Huellas selladas al cerrar v1: los holdout de S1-S9 no se pueden haber tocado al ampliar la batería.
V1_HOLDOUT_SHA256 = {
    "s1_sencillas.holdout.yaml": "1d161be962d1d8431f6f4dd52acc28896057aa70a562775ca24b792061119657",
    "s2_ambiguas.holdout.yaml": "ce65b3e655038975462c9c8ebf2b79c4f045f8c4a284100d755c49a80a046ba0",
    "s3_encadenadas.holdout.yaml": "581f30e6245902e0a1d83784701863fdf5ab66e1812870e89633b6361e8c671d",
    "s4_complejas.holdout.yaml": "9df10185939109e2fc8cf556a44e73d3ef7c662880c5534c38c552d715a5142a",
    "s5_sin_datos.holdout.yaml": "f6151c081c07bff119b57f52b2e811449f16a8fc7403ef8c6e3d73c1f1bc0516",
    "s6_adversariales.holdout.yaml": "abda58c890f6d45ac1f1e6167c652fe870db1774802243bc793536d29795cfa0",
    "s7_multicompetencia.holdout.yaml": "aaa561a14631ba646efcf4751b158396482eff75795077d652fa8fb369b56fff",
    "s8_estadisticas.holdout.yaml": "d1e550b0f01e417c2e2b4652c550b5216afb695a174f2b24b56d3c20991d9dbc",
    "s9_documentales.holdout.yaml": "c81cea1cf0f7314c8d0d4d8c2e17121b63bca983d0984ca5b07e96634bdc065f",
}
LITERAL_QUESTIONS = (
    "¿Sabes en qué equipo juega Pablo silvosa?", "Creo que juega en el San Felipe Neri",
    "¿Sabes qué partidos ha pitado el árbitro Álvaro Sánchez Suárez?",
    "¿Puede sacar el portero y que pase el medio del campo el balón sin botar?", "¿No hay ninguna limitación de saque del portero?",
    "Dime todos los partidos que se juegan el sábado 7 de noviembre por la tarde", "De todas",
    "¿Cómo han sido los resultados de todos los equipos del Álamo?", "De todos en todas las competiciones",
    "¿Cuál es el máximo goleador de todas las ligas senior?")


def new_cases(split: str = "all") -> list[Case]:
    return [c for c in load_cases(ROOT, split) if c.stratum == "S10" or "_extra" in c.source_file]


def test_s10_size_split_and_literal_questions() -> None:
    s10 = [c for c in load_cases(ROOT, "all") if c.stratum == "S10"]
    assert len(s10) == 60 and sum(c.holdout for c in s10) == 18  # 30 % holdout
    assert all(c.source_file == ("s10_multiambito.holdout.yaml" if c.holdout else "s10_multiambito.dev.yaml") for c in s10)
    dev_questions = {t.user for c in s10 if not c.holdout for t in c.turns}
    assert set(LITERAL_QUESTIONS) <= dev_questions  # las preguntas reales, con sus erratas, son casos dev
    assert not [c.id for c in s10 if c.holdout and any(t.user in LITERAL_QUESTIONS for t in c.turns)]
    assert counts(s10)["S10"] == {"cases": len(s10), "turns": sum(len(c.turns) for c in s10), "holdout": 18}
    convs = [c for c in s10 if c.conversation]
    assert len(convs) >= 8  # los seguimientos son conversaciones multi-turno
    extras = [c for c in load_cases(ROOT, "all") if "_extra" in c.source_file]
    assert 35 <= len(extras) <= 45 and not any(c.holdout for c in extras)  # solo dev: los holdout sellados no se tocan


def test_old_holdout_fingerprints_are_unchanged_and_s10_is_sealed() -> None:
    ddir = dataset_dir(ROOT)
    for name, digest in V1_HOLDOUT_SHA256.items():
        assert hashlib.sha256((ddir / name).read_bytes()).hexdigest() == digest, name
    assert verify_seals(ROOT) == []
    import json

    sealed = json.loads((ddir / "SEALS.json").read_text(encoding="utf-8"))["files"]
    assert {n: sealed[n]["sha256"] for n in V1_HOLDOUT_SHA256} == V1_HOLDOUT_SHA256  # los sellos antiguos siguen idénticos
    assert sealed["s10_multiambito.holdout.yaml"]["cases"] == 18


def test_new_cases_cover_the_requested_casuistry() -> None:
    cases = {c.id: c for c in new_cases()}
    kinds = {spec["kind"] for c in cases.values() for spec in c.select.values()}
    assert {"player", "player_lookalike", "referee", "referee_lookalike", "day", "day_absent", "club_multi", "club_ambiguous",
            "scorer_ranking", "team_ranking", "category_pair"} <= kinds <= set(SELECT_KINDS)
    for prefix, minimum in (("s10_jug_", 10), ("s10_arb_", 6), ("s10_fecha_", 10), ("s10_club_", 6), ("s10_rank_", 8), ("s10_reg_", 6),
                            ("s10_temporada_", 2), ("s10_todos_", 2), ("s10_aclarar_", 2)):
        assert sum(i.startswith(prefix) for i in cases) >= minimum, prefix
    for needle in ("sin_acentos", "un_apellido", "homonimo", "inexistente", "premisa_falsa", "premisa_verdadera", "pabellon", "sin_partidos",
                   "futura", "manana", "tarde", "fin_de_semana", "genero", "categoria", "a_b_c", "ambiguo", "racha", "comparativa", "cesion",
                   "faltas_acumuladas", "tiempo_muerto", "expulsion"):
        assert any(needle in i for i in cases), needle
    behaviors = {t.expect["behavior"] for c in cases.values() for t in c.turns}
    assert behaviors == {"answer", "clarify", "abstain", "offtopic"}
    users = " ".join(t.user.lower() for c in cases.values() for t in c.turns)
    for needle in ("how many", "quants", "quin ", "🙏", "canarias", "putnos", "instrucciones del sistema", "aplazado", "suspendido", "jornada"):
        assert needle in users, needle


def test_every_new_case_has_sql_truth_and_a_v2_target_when_v1_cannot_answer() -> None:
    for c in new_cases():
        bound = set(c.select)
        for t in c.turns:
            used = referenced(t.user) | referenced(t.expect)
            assert used <= bound, (c.id, used - bound)  # ninguna plantilla sin entidad elegida del snapshot
            for spec in c.select.values():
                assert spec["kind"] in SELECT_KINDS
            kinds = {s["kind"] for s in c.select.values()}
            if t.expect["behavior"] in ("answer", "clarify") and not kinds:
                assert t.expect.get("source") == "rules" or t.expect.get("facts") and all(f["kind"] == "text_any" for f in t.expect["facts"]), c.id
            if c.capability == "v2":
                v2 = t.expect.get("v2")
                assert v2 and v2["behavior"] in ("answer", "clarify", "abstain"), c.id
                assert t.expect["behavior"] in ("abstain", "clarify")  # en v1: abstención honesta o aclaración, nunca un dato
                if v2["behavior"] == "answer":
                    assert v2.get("facts"), c.id  # el hecho esperado sale de la verdad SQL
                    assert all(PLACEHOLDER.search(str(f.get("value", f.get("any")))) for f in v2["facts"]
                               if f["kind"] != "text_any"), c.id
        if c.capability == "v2":
            assert c.stratum == "S10"


@pytest.fixture(scope="module")
def world(synthetic: Any) -> World:
    return build_world(synthetic, "all")


# Casos con la frase literal del responsable: se resuelven contra su snapshot real, no contra el sintético (nombres inventados).
REAL_ONLY = {"s10_jug_literal_silvosa", "s10_arb_literal_inexistente_parecido"}


def test_selector_resolves_every_new_case_on_the_synthetic_snapshot(world: World) -> None:
    for c in new_cases():
        rc = world.resolved[c.id]
        if c.id in REAL_ONLY:
            continue
        assert rc.status == "ok", (c.id, rc.reason)  # el sintético incluye árbitros, homónimos, partidos aplazados, actas abiertas, etc.
        for t in rc.turns:
            assert not PLACEHOLDER.search(t.user), t.user
            if t.v2 is not None:
                assert not PLACEHOLDER.search(str(t.v2)), t.v2
    assert world.turn("s10_fecha_literal_sabado_tarde").v2["facts"][0]["value"] == 1  # un partido por la tarde el 7 de noviembre


def test_literal_person_cases_are_not_evaluable_without_the_real_snapshot(world: World) -> None:
    for cid in sorted(REAL_ONLY):
        rc = world.resolved[cid]
        assert rc.status == "not_evaluable" and rc.reason, (cid, rc.status)


def test_person_fragment_selectors_with_invented_names(world: World) -> None:
    """Misma lógica que los dos casos literales, con nombres inventados presentes en el sintético."""
    from dataclasses import replace

    from futsal.evals.selector import resolve_case

    by_id = {c.id: c for c in new_cases()}
    jug = replace(by_id["s10_jug_literal_silvosa"], select={"player": {"kind": "player", "name_like": "QUINTANAR"}})
    arb = replace(by_id["s10_arb_literal_inexistente_parecido"],
                  select={"ref": {"kind": "referee_lookalike", "like": "BERMEJO SUAREZ", "first": "Álvaro"}})
    caps = world.catalog.capabilities()
    lit = resolve_case(jug, world.catalog, caps)
    assert lit.status == "ok", lit.reason
    assert lit.bindings["player"]["name"] == "QUINTANAR PEREZ, PABLO" and lit.turns[0].v2["facts"][0]["value"] == lit.bindings["player"]["team"]
    look = resolve_case(arb, world.catalog, caps)
    assert look.status == "ok", look.reason
    assert look.bindings["ref"]["name"] == "BERMEJO SUAREZ, ÁLVARO" and look.bindings["ref"]["real_name"] == "BERMEJO SUAREZ, HUGO"


def test_plan_counts_the_new_stratum(synthetic: Any) -> None:
    plan = build_plan("futsito-v1", "all", 1, synthetic.root)
    assert plan["by_stratum"]["S10"] == {"cases": 60, "turns": plan["by_stratum"]["S10"]["turns"], "holdout": 18}
    assert plan["by_stratum"]["S10"]["turns"] > 60 and set(plan["by_stratum"]) >= {f"S{i}" for i in range(1, 11)}
    dev = build_plan("futsito-v1", "dev", 1, synthetic.root)
    assert dev["by_stratum"]["S10"]["cases"] == 42 and dev["by_stratum"]["S10"]["holdout"] == 0


# ----------------------------------------------------------------------------- verdad SQL
def test_new_truth_queries_are_read_only_named_sql() -> None:
    from futsal.evals.catalog import sql_text

    names = {p.stem for p in TRUTH_DIR.glob("*.sql")}
    assert {"player_teams", "officials", "match_times", "scorers_ranking", "club_teams"} <= names
    for n in ("player_teams", "officials", "match_times", "scorers_ranking", "club_teams"):
        assert sql_text(n).lstrip().lower().startswith(("--", "select", "with"))


@pytest.fixture(scope="module")
def cat(synthetic: Any) -> Catalog:
    from world import AS_OF

    from futsal.evals.snapshot import parse_as_of

    return Catalog.load(synthetic.engine, seed="t", as_of=parse_as_of(AS_OF))


def test_sql_club_base_and_category_ranking_agree_with_the_classifiers(cat: Catalog) -> None:
    assert {t["team_id"] for t in cat.teams} == set(cat.club_bases)
    assert [t["name"] for t in cat.teams if cat.club_bases[t["team_id"]] != base_name(t["name"])] == []  # SQL = catalog.base_name
    assert cat.scorer_ranking and all(r["category"] == cat.group(r["group_id"])["category"] for r in cat.scorer_ranking)
    assert {r["gender"] for r in cat.scorer_ranking} == {"femenino", "masculino"}
    assert {r["category"] for r in cat.scorer_ranking if r["category_rank"] == 1} == {"senior", "juvenil", "femenino"}


def test_resolved_facts_equal_independent_sql_counts(synthetic: Any, world: World) -> None:
    with synthetic.engine.connect() as c:
        ref = world.b("s10_arb_cuantos_partidos", "ref")
        n = c.execute(text(
            "SELECT count(DISTINCT r.match_id) FROM match_officials o JOIN match_report_observations ob ON ob.id = o.observation_id "
            "JOIN match_reports r ON r.id = ob.report_id WHERE o.display_name = :n AND o.role = 'ARBITRO'"), {"n": ref["name"]}).scalar_one()
        assert n == ref["matches_n"]
        top = world.b("s10_arb_mas_partidos", "ref")
        per = {r[0]: r[1] for r in c.execute(text(
            "SELECT o.display_name, count(DISTINCT r.match_id) FROM match_officials o JOIN match_report_observations ob ON ob.id = o.observation_id "
            "JOIN match_reports r ON r.id = ob.report_id WHERE o.role = 'ARBITRO' GROUP BY 1"))}
        assert max(per, key=per.get) == top["name"] and per[top["name"]] == top["matches_n"]  # type: ignore[arg-type]
        day = world.b("s10_fecha_un_dia", "day")
        assert c.execute(text("SELECT count(*) FROM matches WHERE scheduled_date = :d"), {"d": day["date"]}).scalar_one() == day["count"]
        absent = world.b("s10_fecha_sin_partidos", "day")
        assert absent["count"] == 0 == c.execute(text("SELECT count(*) FROM matches WHERE scheduled_date = :d"), {"d": absent["date"]}).scalar_one()
        afternoon = world.b("s10_fecha_literal_sabado_tarde", "day")
        assert afternoon["count"] == c.execute(text(
            "SELECT count(*) FROM matches WHERE scheduled_date = '2026-11-07' AND scheduled_time >= '16:00'")).scalar_one()
        senior = world.b("s10_rank_literal_goleador_senior", "r")["top"]
        best = c.execute(text(
            "SELECT r.player_name, r.goals FROM scorer_rows r JOIN scorer_snapshots s ON s.id = r.snapshot_id "
            "JOIN competition_groups g ON g.id = s.group_id JOIN competitions c ON c.id = g.competition_id "
            "WHERE c.name NOT ILIKE '%FEMENIN%' AND c.name NOT ILIKE '%JUVENIL%' AND c.name NOT ILIKE '%CADETE%' "
            "ORDER BY r.goals DESC, r.player_name LIMIT 1")).one()
        assert (best[0], best[1]) == (senior["name"], senior["goals"])
        homonyms = named_query(c, "player_teams")
        david = [r for r in homonyms if r["display_name"] == "GARCIA CASADO, DAVID"]
        assert len({r["team_id"] for r in david}) == 2  # el homónimo de jugador en dos equipos existe en la verdad SQL


def test_homonym_players_and_lookalikes_follow_the_catalog(cat: Catalog) -> None:
    players = {p["name"]: p for p in cat.player_entities()}
    assert "QUINTANAR PEREZ, PABLO" in players and players["QUINTANAR PEREZ, PABLO"]["natural"] == "Pablo Quintanar Perez"
    homonyms = [p for p in cat.player_entities() if p["name"] == "GARCIA CASADO, DAVID"]
    assert len(homonyms) == 2 and all(p["homonym_count"] == 1 and len(p["decoys"]) == 1 for p in homonyms)
    assert homonyms[0]["team"] != homonyms[1]["team"]
    real = next(r for r in cat.referee_entities() if r["name"] == "BERMEJO SUAREZ, HUGO")
    fake = cat.lookalikes(real, ("Álvaro", "Hugo"))
    assert [f["natural"] for f in fake] == ["Álvaro Bermejo Suarez"]  # el nombre real se descarta; el verosímil no existe en el snapshot
    assert tokens_key(fake[0]["name"]) not in cat.person_keys()  # ni jugador ni oficial con esas palabras, en ningún orden


def test_null_round_and_missing_time_never_raise_and_resolve_or_are_not_evaluable() -> None:
    """Regresión con datos reales: jornada nula (copas de eliminatoria), hora desconocida, partidos sin marcador ni acta."""
    from datetime import datetime

    from futsal.evals.model import parse_case
    from futsal.evals.selector import resolve_case as resolve

    groups = [{"group_id": 1, "group_name": "Eliminatorias", "competition_id": 1, "competition": "COPA SENIOR I - fase previa- FS", "season_id": 1,
               "season": "22", "teams": 2, "finished": 1, "pending": 1, "reports": 0, "has_standings": False, "has_scorers": False}]
    teams = [{"team_id": 1, "name": "C.D. UNO", "group_id": 1}, {"team_id": 2, "name": "C.D. DOS", "group_id": 1}]
    base = {"group_id": 1, "round": None, "home_team_id": 1, "away_team_id": 2, "home": "C.D. UNO", "away": "C.D. DOS", "venue": None,
            "has_report": False}
    matches = [{**base, "match_id": 1, "status": "finished", "home_score": 2, "away_score": 1, "day": datetime(2026, 11, 7).date()},
               {**base, "match_id": 2, "status": "scheduled", "home_score": None, "away_score": None, "day": datetime(2026, 11, 14).date()}]
    times = {1: {"kickoff_time": None, "slot": "sin_hora", "report_closed": None}, 2: {"kickoff_time": None, "slot": "sin_hora", "report_closed": None}}
    cat = Catalog(groups, teams, matches, {}, {}, {}, frozenset(), [], "seed", datetime(2026, 11, 9, 22, 0), times=times)
    ent = cat.group_entity(cat.group(1))
    assert "last_round" not in ent and ent["finished"] == 1 and ent["weekend_first_timed"] == 0 and "open_round" not in ent
    team = cat.team_entity(teams[0])
    assert team["next_match"]["round"] is None and team["next_has_time"] == 0 and team["next_has_venue"] == 0
    assert cat.kickoff_times(2) == {} and cat.team_rank_entity("senior", "win_streak") is None
    assert cat.day_entity(["2026-11-07"], "sábado 7 de noviembre", slot="tarde")["count"] == 0
    for kind, spec in (("group", {"kind": "group", "where": {"last_round": ">=1"}}), ("team", {"kind": "team"}),
                       ("day", {"kind": "day", "slot": "tarde", "clean_slot": True, "where": {"count": ">=1"}})):
        case = parse_case({"id": f"x_{kind}", "stratum": "S10", "holdout": False, "requires": [], "capability": "v1", "select": {"e": spec},
                           "turns": [{"user": "¿{e.name} {e.last_round}?", "expect": {"behavior": "abstain"}}]})
        rc = resolve(case, cat)
        assert rc.status == "not_evaluable" and rc.reason  # nunca una excepción: se resuelve o queda no evaluable con motivo
