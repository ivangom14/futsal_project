"""Parsers de clasificación y goleadores de la RFFM sobre fixtures sintéticas con la estructura real (sin red)."""
from pathlib import Path

import pytest

from futsal.ingestion.rffm.tables import (
    IdentityMismatch,
    StructureChanged,
    TransientSource,
    parse_scorers,
    parse_standings,
)

FIX = Path("tests/fixtures/rffm_tables")
COMP, GROUP = "90000001", "90000002"


def _html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_full_standings_keep_the_published_fields_and_never_recompute() -> None:
    t = parse_standings(_html("standings_round4_full.html"), competition=COMP, group=GROUP)
    assert (t.outcome, t.round_number, t.current_round, str(t.round_date), len(t.rows), t.warnings) == ("ok", 4, 4, "2099-10-17", 6, [])
    first = t.rows[0]
    assert (first.position, first.team_external_id, first.team_name, first.played, first.won, first.drawn, first.lost) == (
        1, "91000001", "C.D. EJEMPLO UNO", 4, 3, 1, 0)
    assert (first.goals_for, first.goals_against, first.points, first.penalty_points) == (19, 11, 10, 0)
    assert first.details["jugados_casa"] == 2 and first.details["racha_partidos"][0]["tipo"] == "G" and "url_img" not in first.details
    assert [p["name"] for p in t.promotions] == ["ASCENSO", "DESCENSO"]
    # la clasificación publicada se conserva tal cual: la posición 2 (9 pts, 3 PJ) va por delante de la 3 (9 pts, 4 PJ)
    assert [r.position for r in t.rows] == [1, 2, 3, 4, 5, 6] and t.rows[1].played == 3 and t.rows[2].played == 4


def test_other_round_is_a_different_table_with_its_own_round_number() -> None:
    t = parse_standings(_html("standings_round1.html"), competition=COMP, group=GROUP)
    assert t.round_number == 1 and t.current_round == 4 and t.rows[0].team_name == "A.D. EJEMPLO TRES" and t.rows[0].played == 1


def test_empty_standings_are_a_valid_empty_table_not_an_error() -> None:
    t = parse_standings(_html("standings_empty.html"), competition=COMP, group=GROUP)
    assert t.outcome == "empty" and t.rows == [] and t.round_number == 1


def test_null_standings_are_transient_never_an_empty_table() -> None:
    with pytest.raises(TransientSource):
        parse_standings(_html("standings_null.html"))


def test_knockout_groups_have_no_standings_table() -> None:
    t = parse_standings(_html("standings_cup.html"), competition=COMP, group=GROUP)
    assert t.outcome == "not_applicable" and t.rows == [] and t.round_number is None


def test_unexpected_structure_and_wrong_group_are_detected() -> None:
    with pytest.raises(StructureChanged, match="puntos"):
        parse_standings(_html("standings_structure_changed.html"))
    with pytest.raises(IdentityMismatch):
        parse_standings(_html("standings_other_group.html"), competition=COMP, group=GROUP)
    with pytest.raises(StructureChanged):
        parse_standings("<html><body>sin datos</body></html>")


def test_a_non_numeric_row_is_dropped_and_reported_so_the_table_is_incomplete() -> None:
    t = parse_standings(_html("standings_incomplete_row.html"))
    assert len(t.rows) == 5 and t.warnings and "descartada" in t.warnings[0]


def test_scorers_keep_published_order_ties_and_same_names_apart() -> None:
    t = parse_scorers(_html("scorers_full.html"), competition=COMP, group=GROUP)
    assert (t.outcome, len(t.rows), t.warnings) == ("ok", 6, [])
    assert [r.goals for r in t.rows] == [6, 4, 4, 4, 2, 1] and [r.row_order for r in t.rows] == [1, 2, 3, 4, 5, 6]
    same_name = [r for r in t.rows if r.player_name == "GARCIA LOPEZ, ANA"]
    assert {r.player_external_id for r in same_name} == {"7000001", "7000004"} and {r.team_external_id for r in same_name} == {"91000001", "91000004"}
    assert t.rows[2].penalty_goals == 1 and t.rows[0].goals_per_match == "1.50"


def test_scorers_empty_null_structure_and_incomplete_rows() -> None:
    assert parse_scorers(_html("scorers_empty.html")).outcome == "empty"
    with pytest.raises(TransientSource):
        parse_scorers(_html("scorers_null.html"))
    with pytest.raises(StructureChanged, match="goles"):
        parse_scorers(_html("scorers_structure_changed.html"))
    t = parse_scorers(_html("scorers_incomplete_row.html"))
    assert len(t.rows) == 5 and "descartada" in t.warnings[0]
    with pytest.raises(IdentityMismatch):
        parse_scorers(_html("scorers_full.html"), group="90000999")
