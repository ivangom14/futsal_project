from pathlib import Path
from typing import Any

from futsal.ingestion.rffm.parser import extract_page_props
from futsal.ingestion.rffm.scope import season_code_from_name, validate_scope

FX = Path(__file__).parents[1] / "fixtures" / "rffm_v2"


def props(name: str) -> dict[str, Any]:
    return extract_page_props((FX / name).read_text(encoding="utf-8"))


def test_mismatch_page_is_detected_in_season_and_game_type() -> None:
    check = validate_scope(props("resultados_scope_mismatch_pedido_s21.html"), {"season": "21", "game_type": "3"})
    assert not check.ok
    assert any("cod_temporada" in m and "'22'" in m for m in check.mismatches)
    assert any("idGameType" in m for m in check.mismatches)


def test_mismatch_page_even_if_query_echoes_the_request() -> None:
    # query.temporada=21 es solo el eco; la temporada servida (22) manda
    p = props("resultados_scope_mismatch_pedido_s21.html")
    assert p["query"]["temporada"] == "21"
    assert not validate_scope(p, {"season": "21", "game_type": "3"}).ok


def test_calendar_scope_ok_and_wrong_season_name() -> None:
    p = props("calendario_s22_c26738243_g26738245.html")
    req = {"season": "22", "game_type": "3", "competition": "26738243", "group": "26738245"}
    assert validate_scope(p, req).ok
    bad = validate_scope(p, {**req, "season": "21"})
    assert not bad.ok and any("calendar.temporada" in m for m in bad.mismatches)
    assert validate_scope(props("calendario_s10_c18329896_g18298068.html"),
                          {"season": "10", "game_type": "3", "group": "18298068"}).ok


def test_requested_competition_and_group_are_checked_and_unverifiable_is_a_mismatch() -> None:
    p = props("calendario_s22_c26738243_g26738245.html")
    assert not validate_scope(p, {"season": "22", "game_type": "3", "group": "999"}).ok
    assert not validate_scope(p, {"season": "22", "game_type": "3", "competition": "1"}).ok
    check = validate_scope({"results": {}}, {"season": "22", "game_type": "3"})
    assert not check.ok and len(check.mismatches) == 2


def test_names_are_compared_when_requested() -> None:
    p = props("calendario_s10_c18329896_g18298068.html")
    base = {"season": "10", "game_type": "3"}
    assert validate_scope(p, {**base, "group_name": "Eliminatorias", "competition_name": "Copa Senior Regional - F.S."}).ok
    assert not validate_scope(p, {**base, "group_name": "Grupo 1"}).ok


def test_results_page_props_scope() -> None:
    p = {"season": {"cod_temporada": "21"}, "idGameType": "3", "query": {},
         "results": {"codigo_competicion": "5", "codigo_grupo": "6"}}
    assert validate_scope(p, {"season": 21, "game_type": 3, "competition": "5", "group": "6"}).ok
    assert not validate_scope(p, {"season": "21", "game_type": "3", "group": "7"}).ok


def test_season_code_from_name() -> None:
    assert season_code_from_name("2026-2027") == "22"
    assert season_code_from_name("2014-2015") == "10"
    assert season_code_from_name("2014-2016") is None
    assert season_code_from_name("x", [{"nombre": "x", "cod_temporada": "99"}]) == "99"
