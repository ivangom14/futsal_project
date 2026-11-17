"""Paginación conversacional v2 (determinista): qué es un seguimiento de un listado y cómo se compone cada página."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from futsal.agent.v2.paging import Listing, listing_from_call, page_text, parse_followup

NOW = datetime(2026, 11, 12, 10, tzinfo=ZoneInfo("Europe/Madrid"))  # jueves
COMPS = ["PREFERENTE AFICIONADO FS", "PREFERENTE JUVENIL FS", "PRIMERA AFICIONADO FS", "TERCERA DIVISIÓN FS"]
DAY = Listing("matches_by_date", {"date_from": "2026-11-14", "time_from": "15:00", "time_to": "23:59"}, 0, 10, 48, 10)


def fu(text: str, listing: Listing = DAY):  # type: ignore[no-untyped-def]
    return parse_followup(text, listing, COMPS, lambda: NOW)


@pytest.mark.parametrize(("text", "reason", "offset", "limit"), [
    ("Dime los siguientes", "next", 10, 10), ("los siguientes", "next", 10, 10), ("Muéstrame 10 más", "next", 10, 10),
    ("dame 20 más", "next", 10, 20), ("Continúa", "next", 10, 10), ("¿Y el resto?", "rest", 10, 38), ("muéstrame 25", "size", 0, 25),
    ("Ordénalos por hora", "order", 0, 10)])
def test_page_commands(text: str, reason: str, offset: int, limit: int) -> None:
    f = fu(text)
    assert f is not None and (f.reason, f.offset, f.limit) == (reason, offset, limit) and f.args == DAY.args


def test_filters_keep_the_rest_and_restart_from_the_first_page() -> None:
    comp = fu("Ahora solo los de Preferente")
    assert comp is not None and comp.reason == "filter" and comp.offset == 0 and comp.args["competition"] == "preferente"
    assert comp.args["date_from"] == "2026-11-14" and comp.args["time_from"] == "15:00"  # se mantienen el día y la franja
    cat = fu("solo los juveniles")
    assert cat is not None and cat.args["category"] == "juvenil" and cat.offset == 0
    sunday = fu("¿Y el domingo?")
    assert sunday is not None and sunday.args["date_from"] == sunday.args["date_to"] == "2026-11-15" and "time_from" not in sunday.args
    late = fu("solo a partir de las 18")
    assert late is not None and (late.args["time_from"], late.args["time_to"]) == ("18:00", "23:59") and late.limit == 10


@pytest.mark.parametrize("text", ["¿Quién ganó el domingo?", "¿Cuántos puntos tiene el Alamo?", "¿Quién es el máximo goleador de la Preferente?",
                                  "Explícame la regla del saque de banda y qué pasa si el balón sale por la línea de meta tras tocar al portero",
                                  "hola", "gracias"])
def test_new_questions_are_not_hijacked(text: str) -> None:
    assert fu(text) is None


def test_previous_page_and_listing_round_trip() -> None:
    page3 = Listing("scorers_ranking", {"category": "senior"}, 20, 10, 87, 30)
    prev = fu("los anteriores", page3)
    assert prev is not None and (prev.reason, prev.offset) == ("prev", 10)
    assert Listing.from_dict(page3.to_dict()) == page3 and Listing.from_dict({"tool": "otra", "args": {}}) is None
    body = {"items": [{"rank": 1}], "count": 87, "offset": 0, "next_offset": 10}
    assert listing_from_call("scorers_ranking", {"category": "senior", "limit": 10}, body) == Listing("scorers_ranking", {"category": "senior"}, 0, 10, 87, 10)
    assert listing_from_call("find_official", {"name": "x"}, body) is None  # sin include_matches no es un listado
    assert listing_from_call("find_official", {"name": "x", "include_matches": True}, {**body, "people": [{"name": "a"}]}) is None


def test_page_text_states_the_range_the_total_and_what_comes_next() -> None:
    rows = [{"id": i, "date": "2026-11-14", "time": f"{15 + i}:00", "comp": "PREFERENTE AFICIONADO FS · Grupo 1", "home": f"L{i}", "away": f"V{i}"}
            for i in range(3)]
    mid = page_text("matches_by_date", DAY.args, {"items": rows, "count": 48, "offset": 10, "next_offset": 13}, "next", [])
    assert mid.startswith("Partidos del sábado 14 de noviembre entre las 15:00 y las 23:59: mostrando 11–13 de 48.")
    assert "- 15:00 · L0 – V0 · PREFERENTE AFICIONADO FS · Grupo 1" in mid  # un solo día: la fecha va en la cabecera and mid.endswith("¿Quieres que te muestre los siguientes 10?")
    last = page_text("matches_by_date", DAY.args, {"items": rows[:1], "count": 11, "offset": 10, "next_offset": None}, "next", [])
    assert "mostrando 11 de 11" in last and last.endswith("No hay más resultados.")
    assert "ya te he mostrado los 48" in page_text("matches_by_date", DAY.args, {"items": [], "count": 48, "offset": 48}, "next", [])
    assert "no hay resultados con ese filtro" in page_text("matches_by_date", DAY.args, {"items": [], "count": 0, "offset": 0}, "filter", ["fecha"])
    undated = page_text("matches_by_date", DAY.args, {"items": [{**rows[0], "time": None, "status": "postponed", "score": None}], "count": 1, "offset": 0},
                        "filter", [])
    assert "(hora sin confirmar)" in undated and "aplazado" in undated
