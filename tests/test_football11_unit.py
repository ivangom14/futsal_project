"""Fase 2 · fútbol 11 sin red ni BD: los parsers EXISTENTES con las respuestas anonimizadas del piloto (Preferente Aficionado,
grupo 1), vocabulario del resolutor por deporte, «no consta» frente a «no incorporado», alineación de fútbol 11 y alcance por grupo."""

import json
from pathlib import Path

import pytest

from futsal.agent import presentation as pres
from futsal.agent.v2 import not_in_source as nir
from futsal.config.competition_aliases import load_aliases, load_dimensions
from futsal.domain.competitions import Competition
from futsal.domain.resolver import Resolver
from futsal.domain.sports import mentioned_sports
from futsal.ingestion.queue import ingest_scope as sc
from futsal.ingestion.rffm.report_parser import RffmParseError, parse_match_report
from futsal.ingestion.rffm.results_api import RoundResult, parse_results_api
from futsal.ingestion.rffm.tables import parse_scorers, parse_standings

F11 = Path(__file__).parent / "fixtures" / "rffm_f11"
FS_RESULTS = Path(__file__).parent / "fixtures" / "rffm_v2"
URL = "https://www.rffm.es/acta-partido/5589201?temporada=22&competicion=26737701&grupo=26737702"


def _keys(o: object, p: str = "") -> set[str]:
    if isinstance(o, dict):
        return {p + k for k in o} | {x for k, v in o.items() for x in _keys(v, p + k + ".")}
    if isinstance(o, list) and o:
        return _keys(o[0], p + "[].")
    return set()


def test_results_api_has_the_same_structure_and_parser_as_futsal() -> None:
    f11 = json.loads((F11 / "api_results_g26737702_r10.json").read_text(encoding="utf-8"))
    fs_file = next(iter(sorted(FS_RESULTS.rglob("api_results_*.json"))))
    assert _keys(f11) == _keys(json.loads(fs_file.read_text(encoding="utf-8")))  # mismas 57 claves en los dos deportes
    played = parse_results_api(f11, "26737702", 10)
    assert isinstance(played, RoundResult) and len(played.matches) == 9 and all(m.match.status == "finished" for m in played.matches)
    upcoming = parse_results_api(json.loads((F11 / "api_results_g26737702_r11.json").read_text(encoding="utf-8")), "26737702", 11)
    assert isinstance(upcoming, RoundResult) and all(m.match.status == "scheduled" for m in upcoming.matches)


def test_tables_are_parsed_by_the_futsal_parsers() -> None:
    st = parse_standings((F11 / "clasificaciones_g26737702.html").read_text(encoding="utf-8"), competition="26737701", group="26737702")
    assert st.outcome == "ok" and len(st.rows) == 18 and all(r.points == 3 * r.won + r.drawn for r in st.rows)
    scorers = parse_scorers((F11 / "goleadores_g26737702.html").read_text(encoding="utf-8"), competition="26737701", group="26737702")
    assert scorers.outcome == "ok" and sum(r.goals for r in scorers.rows) == 286
    assert all(r.player_name.endswith("ANÓNIMO") for r in scorers.rows)  # la fixture no contiene nombres reales


def test_football11_report_has_eleven_starters_a_declared_scheme_and_substitutions() -> None:
    rep = parse_match_report((F11 / "acta_5589201.html").read_text(encoding="utf-8"), URL)
    starters = [p for p in rep.players if p.lineup_status == "starter"]
    assert len(starters) == 22 and {sum(p.team_side == s for p in starters) for s in ("home", "away")} == {11}
    assert {t.side: t.formation for t in rep.teams} == {"home": "1-4-3-3", "away": "1-4-4-2"}
    assert rep.validation.unparsed_sections == []  # Fase 3C: sustituciones y otros técnicos interpretados
    subs = [e for e in rep.events if e.event_type == "substitution"]
    assert len(subs) == 10 and all(e.player_external_id and e.related_player_external_id and e.minute is not None for e in subs)
    assert [(x.role, x.role_label) for x in rep.staff if x.role == "other_staff"] == [("other_staff", "ENCARGADO DE MATERIAL")]
    assert sum(e.event_type in ("goal", "own_goal") for e in rep.events) == 6  # 5-1: las sustituciones no suman
    assert [o.role for o in rep.officials] == ["ARBITRO", "ARBITRO ASISTENTE", "ARBITRO ASISTENTE"]


def test_an_empty_report_page_is_rejected_not_invented() -> None:
    with pytest.raises(RffmParseError):  # la web devolvió `game = null` aunque /api/results daba el acta por cerrada
        parse_match_report((F11 / "acta_5589202_vacia.html").read_text(encoding="utf-8"), URL.replace("5589201", "5589202"))


def _f11_catalog() -> list[Competition]:
    raw = json.loads((F11 / "api_competitions_s22_t1.json").read_text(encoding="utf-8"))
    return [Competition(i + 1, c["nombre"], ((100 + i, "Grupo 1"),)) for i, c in enumerate(raw)]


def test_absent_terms_depend_on_the_active_catalog() -> None:
    f11 = Resolver(_f11_catalog(), load_aliases(), load_dimensions())
    for q, name in (("clasificación de segunda aficionado", "SEGUNDA AFICIONADO"), ("liga nacional juvenil", "NACIONAL JUVENIL"),
                    ("preferente aficionado", "PREFERENTE AFICIONADO")):
        p = f11.parse(q)
        assert p.absent == () and name in [c.name for c in f11.match(p)], q
    assert f11.parse("segunda b").absent == ("segunda b",)  # sigue sin existir
    assert f11.parse("división de honor").absent == ()  # «DIVISIÓN DE HONOR CADETE» sí está en el catálogo de fútbol 11
    fs = Resolver([Competition(1, "PREFERENTE AFICIONADO FS", ((1, "Grupo 1"),))], load_aliases(), load_dimensions())
    assert "segunda" in fs.absent_phrases and "segunda" not in f11.absent_phrases


def test_homonymous_competitions_resolve_within_their_own_sport_catalog() -> None:
    f11 = Resolver(_f11_catalog(), load_aliases(), load_dimensions())
    fs = Resolver([Competition(1, "PREFERENTE AFICIONADO FS", ((1, "Grupo 1"),)), Competition(2, "PREFERENTE JUVENIL FS", ((2, "Grupo 1"),))],
                  load_aliases(), load_dimensions())
    assert [c.name for c in f11.match(f11.parse("Preferente Aficionado"))] == ["PREFERENTE AFICIONADO"]
    assert [c.name for c in fs.match(fs.parse("Preferente Aficionado"))] == ["PREFERENTE AFICIONADO FS"]


@pytest.mark.parametrize("text,sports", [
    ("¿Cómo va el DOSA en fútbol sala?", {"futsal"}), ("Clasificación de Preferente Aficionado de fútbol", {"football11"}),
    ("partidos de futbol 11 del sábado", {"football11"}), ("¿Y en futsal?", {"futsal"}), ("fútbol 7 alevín", set()),
    ("¿Quién lidera la Preferente?", set()), ("de fútbol y de fútbol sala", {"futsal", "football11"}),
])
def test_explicit_sport_mentions(text: str, sports: set[str]) -> None:
    assert mentioned_sports(text) == sports


def test_club_names_with_futbol_are_not_sport_mentions() -> None:
    assert mentioned_sports("¿Cómo va la Escuela de Fútbol Aluche?", ignore=("ESCUELA DE FUTBOL ALUCHE",)) == frozenset()


def test_substitutions_are_not_in_futsal_reports_but_are_available_in_football11() -> None:
    q = "¿Qué sustituciones hubo en el partido?"
    assert "no recoge las sustituciones" in nir.answer(nir.detect(q, "futsal"))  # type: ignore[arg-type]
    assert nir.detect(q, "football11") is None  # Fase 3C: dato disponible → lo responden las herramientas


def test_futsal_report_hash_is_unchanged_by_the_new_sections() -> None:
    """Las actas de fútbol sala no traen sustituciones ni otros técnicos: su contenido normalizado (y su hash) no cambia, así que
    reimportarlas no crea observaciones nuevas."""
    fs = (Path(__file__).parent / "fixtures" / "rffm_match_report.html").read_text(encoding="utf-8")
    rep = parse_match_report(fs, "https://www.rffm.es/acta-partido/5575697?temporada=22&competicion=26738243&grupo=26738245")
    assert rep.report.content_hash == "27c92f3e6341875f4567a2c48f1d9d39070437bd5e5ba0f4aefa512a45d412b2"


def _report_payload() -> dict[str, object]:
    players = [{"team": "LOCAL CF", "name": f"JUGADOR {i:03d}, ANÓNIMO", "shirt": i, "lineup": "starter" if i <= 11 else "substitute",
                "goalkeeper": i == 1} for i in range(1, 19)]
    return {"report_available": True, "players": players, "report": {"home_formation": "1-4-3-3", "away_formation": "1-4-4-2"},
            "match": {"home_team": "LOCAL CF", "away_team": "VISITANTE CF", "status": "finished", "home_score": 5, "away_score": 1}}


def test_football11_lineup_is_a_list_with_the_declared_scheme_never_a_futsal_court() -> None:
    [v] = pres.lineup_from_report(_report_payload(), "¿Quién jugó de titular?", {}, "football11")
    assert v["court"] is False and v["sport"] == "football11" and len(v["starters"]) == 11 and len(v["substitutes"]) == 7
    assert "1-4-3-3" in v["note"] and "pista" not in v["note"]
    [fs] = pres.lineup_from_report(_report_payload(), "¿Quién jugó de titular?", {})  # sin deporte: formato de siempre (fútbol sala)
    assert "sport" not in fs and fs["note"] == pres.LINEUP_NOTE


def test_scope_entry_can_restrict_groups() -> None:
    raw = json.loads(sc.SCOPE_PATH.read_text(encoding="utf-8"))
    raw["sports"]["football11"]["enabled"] = True
    for e in raw["sports"]["football11"]["competitions"]:
        e["enabled"] = e["name"] == "PREFERENTE AFICIONADO"
        if e["enabled"]:
            e["groups"] = ["26737702"]
    scope = sc.parse_scope(raw)
    assert scope.allows_group("football11", "PREFERENTE AFICIONADO", "40054", "26737702", "Grupo 1")
    assert not scope.allows_group("football11", "PREFERENTE AFICIONADO", "40054", "26737703", "Grupo 2")
    assert scope.allows_group("futsal", "LIGA FS", "1", "123", "Grupo 9")  # fútbol sala: todo, como siempre
