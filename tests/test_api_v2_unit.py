"""API v2: normalización de nombres, nombre base de club y validación de parámetros (sin base de datos)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from futsal.api.app import create_app
from futsal.domain.names import club_base_name, club_base_tokens, norm_name, squad_of, tokens_of


@pytest.mark.parametrize(("text", "expected"), [
    ("Pablo quintanar", ["pablo", "quintanar"]),
    ("QUINTANAR PEREZ, PABLO", ["quintanar", "perez", "pablo"]),
    ("MUÑOZ GÓMEZ, ÁLVARO", ["munoz", "gomez", "alvaro"]),
    ("  O'Brien-Núñez  ", ["o", "brien", "nunez"]),
    ("J.M. GARCÍA", ["j", "m", "garcia"]),
    ("", []),
    ("@@@ ---", []),
    ("漢字", []),
    ("a\x00b", ["a", "b"]),
])
def test_tokens_are_lowercase_unaccented_alphanumeric(text: str, expected: list[str]) -> None:
    assert tokens_of(text) == expected
    assert all(t.isascii() and t.isalnum() for t in tokens_of(text))


def test_token_order_is_irrelevant_for_matching() -> None:
    assert set(tokens_of("Pablo quintanar")) == set(tokens_of("QUINTANAR PEREZ, PABLO")) - {"perez"}
    assert norm_name("  Ñandú   ÚLTIMO ") == "nandu ultimo"


@pytest.mark.parametrize(("name", "base"), [
    ("C.F.S. PINTO 'A'", "pinto"),
    ("PINTO F.S. B", "pinto"),
    ("PINTO C", "pinto"),
    ("CLUB ATLÉTICO PINTO C", "atletico pinto"),
    ("CLUB DEPORTIVO F.S. ALCOBENDAS", "deportivo alcobendas"),
    ("FÚTBOL SALA GETAFE 'B'", "getafe"),
    ("A.D. ALCORCÓN", "ad alcorcon"),
    ("C.D. LOPE DE VEGA 'B'", "lope de vega"),
    ("PARQUE NORTE F.S. - RODILLITO 'B'", "parque norte rodillito"),
    ("MÓSTOLES", "mostoles"),
    ("A", "a"),
    ("C.F.S.", "cfs"),
])
def test_club_base_name_drops_suffix_club_and_punctuation(name: str, base: str) -> None:
    assert club_base_name(name) == base


def test_distinct_clubs_keep_distinct_base_names() -> None:
    assert club_base_name("A.D. MORATALAZ") != club_base_name("U.D. MORATALAZ")
    assert club_base_tokens("PINTO A") == club_base_tokens("C.F.S. PINTO")
    assert club_base_name("ATLETICO PINTO") != club_base_name("PINTO")


@pytest.mark.parametrize(("name", "squad"), [("C.F.S. PINTO 'A'", "A"), ("PINTO F.S. B", "B"), ("PINTO", None), ("C.F.S. PINTO", None),
                                              ("A", None)])
def test_squad_suffix(name: str, squad: str | None) -> None:
    assert squad_of(name) == squad


@pytest.fixture()
def client() -> TestClient:
    # El motor es perezoso: estos casos fallan en validación antes de abrir ninguna conexión.
    return TestClient(create_app(create_engine("postgresql+psycopg://nobody:none@127.0.0.1:1/none")))


@pytest.mark.parametrize("url", [
    "/v2/players", "/v2/players?name=x", "/v2/players?name=ab&limit=0", "/v2/players?name=ab&limit=101", "/v2/players?name=ab&limit=abc",
    "/v2/officials?name=ab&include=bogus", "/v2/officials/ranking?limit=0", "/v2/matches?date_from=ayer", "/v2/matches?time_from=25:99",
    "/v2/matches?limit=1000", "/v2/clubs/search", "/v2/clubs/results?name=a", "/v2/rankings/teams", "/v2/rankings/teams?metric=wins&min_played=0",
    "/v2/rankings/scorers?competition_kind=bogus", "/v2/players?name=ab&category=bogus", "/v2/matches?gender=bogus",
    "/v2/rankings/scorers?category=bogus",
])
def test_invalid_parameters_are_422(client: TestClient, url: str) -> None:
    r = client.get(url)
    assert r.status_code == 422, (url, r.text)
    assert "detail" in r.json()
