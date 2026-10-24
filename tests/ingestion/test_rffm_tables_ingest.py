"""Descarga con caché de las tablas oficiales: reintentos de null, caché, jornadas no disponibles y cobertura (sin red ni PostgreSQL)."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from futsal.config.targets import CATALOG_SCOPE
from futsal.importer.table_service import ingest_tables
from futsal.ingestion.rffm.client import FetchError
from futsal.ingestion.rffm.league import Target
from futsal.ingestion.rffm.table_ingest import cache_path, fetch_table, standings_rounds
from futsal.ingestion.rffm.tables import StandingsTable
from helpers_tables import COMP, GROUP, SEASON, FakeSite, inventory, load

TARGET = Target(SEASON, COMP, GROUP, "3")
NOW = datetime.now(UTC)  # las fechas de modificación de los ficheros son reales: la edad de la caché se mide contra el reloj real
NO_SLEEP = {"sleep": lambda _s: None}


def _fetch(site: FakeSite, tmp_path: Path, kind: str = "standings", **kw: object):  # type: ignore[no-untyped-def]
    return fetch_table(kind, TARGET, site, cache_path(tmp_path, COMP, GROUP, kind, kw.get("round_number")),  # type: ignore[arg-type]
                       now=lambda: NOW, **NO_SLEEP, **kw)  # type: ignore[arg-type]


def test_null_is_retried_and_a_later_valid_page_is_used_and_cached(tmp_path: Path) -> None:
    site = FakeSite({("clasificaciones", GROUP, None): [load("standings_null.html"), load("standings_null.html"), load("standings_round4_full.html")]})
    got = _fetch(site, tmp_path)
    assert got.ok and isinstance(got.table, StandingsTable) and got.table.round_number == 4 and site.requests == 3
    assert cache_path(tmp_path, COMP, GROUP, "standings").exists()


def test_a_persistent_null_is_an_error_never_an_empty_table_and_is_not_cached(tmp_path: Path) -> None:
    site = FakeSite({("goleadores", GROUP, None): load("scorers_null.html")})
    got = _fetch(site, tmp_path, "scorers", null_retries=2)
    assert not got.ok and got.table is None and got.error_code == "transient" and site.requests == 3
    assert not cache_path(tmp_path, COMP, GROUP, "scorers").exists()


def test_http_errors_structure_changes_and_wrong_group_are_classified_and_not_cached(tmp_path: Path) -> None:
    assert _fetch(FakeSite({}), tmp_path).error_code == "download"
    assert _fetch(FakeSite({("clasificaciones", GROUP, None): load("standings_structure_changed.html")}), tmp_path).error_code == "structure"
    assert _fetch(FakeSite({("clasificaciones", GROUP, None): load("standings_other_group.html")}), tmp_path).error_code == "identity"
    assert not list(tmp_path.rglob("*.html"))


def test_cache_is_reused_until_it_is_old_or_refresh_is_requested(tmp_path: Path) -> None:
    site = FakeSite({("clasificaciones", GROUP, None): load("standings_round4_full.html")})
    first = _fetch(site, tmp_path, max_age_hours=12)
    again = _fetch(site, tmp_path, max_age_hours=12)
    assert first.ok and again.ok and again.from_cache and site.requests == 1
    assert not _fetch(site, tmp_path, max_age_hours=12, refresh=True).from_cache and site.requests == 2
    path = cache_path(tmp_path, COMP, GROUP, "standings")
    old = (NOW - timedelta(hours=30)).timestamp()
    import os
    os.utime(path, (old, old))
    assert not _fetch(site, tmp_path, max_age_hours=12).from_cache and site.requests == 3          # caducada
    os.utime(path, (old, old))
    assert _fetch(site, tmp_path, max_age_hours=None).from_cache and site.requests == 3            # histórico: no caduca
    offline = _fetch(FakeSite({}), tmp_path / "otra", cache_only=True)
    assert offline.error_code == "download" and "offline" in (offline.error or "")


def test_a_round_the_web_answers_with_another_round_is_unavailable_and_not_labelled_as_requested(tmp_path: Path) -> None:
    site = FakeSite({("clasificaciones", GROUP, "9"): load("standings_round4_full.html"),
                     ("clasificaciones", GROUP, "1"): load("standings_round1.html")})
    bad = _fetch(site, tmp_path, round_number=9)
    assert bad.error_code == "round_unavailable" and "devolvió la 4" in (bad.error or "")
    assert not cache_path(tmp_path, COMP, GROUP, "standings", 9).exists()
    ok = _fetch(site, tmp_path, round_number=1)
    assert ok.ok and isinstance(ok.table, StandingsTable) and ok.table.round_number == 1


def test_which_rounds_are_requested() -> None:
    assert standings_rounds(4, round_number=None, all_rounds=False) == [None]
    assert standings_rounds(4, round_number=2, all_rounds=False) == [2]
    assert standings_rounds(4, round_number=None, all_rounds=True) == [None, 1, 2, 3]
    assert standings_rounds(None, round_number=None, all_rounds=True) == [None]


def test_dry_run_isolates_failures_per_group_and_reports_each_one(tmp_path: Path) -> None:
    other = "90000003"
    groups = [{"external_id": GROUP, "name": "Grupo 1", "expected_rounds": 10, "expected_teams": 6},
              {"external_id": other, "name": "Grupo 2", "expected_rounds": 10, "expected_teams": 6}]
    site = FakeSite({("clasificaciones", GROUP, None): load("standings_round4_full.html"), ("clasificaciones", other, None): FetchError("HTTP 500")})
    rep = ingest_tables(None, "standings", inventory(groups), tmp_path, site, dry_run=True, now=lambda: NOW, **NO_SLEEP)
    by = {g["group_id"]: g for g in rep["groups"]}
    assert by[GROUP]["status"] == "ok" and by[GROUP]["rounds"][0]["rows"] == 6
    assert by[other]["status"] == "failed" and by[other]["rounds"][0]["error_code"] == "download"
    assert (tmp_path / "tables-standings-report.json").exists()


def test_cup_groups_are_not_applicable_and_empty_tables_are_valid(tmp_path: Path) -> None:
    site = FakeSite({("clasificaciones", GROUP, None): load("standings_cup.html"), ("goleadores", GROUP, None): load("scorers_empty.html")})
    cup = ingest_tables(None, "standings", inventory(), tmp_path, site, dry_run=True, now=lambda: NOW)
    assert cup["groups"][0]["status"] == "not_applicable"
    empty = ingest_tables(None, "scorers", inventory(), tmp_path, site, dry_run=True, now=lambda: NOW)
    assert empty["groups"][0]["status"] == "empty" and empty["groups"][0]["rounds"][0]["rows"] == 0


def test_incomplete_tables_are_flagged_not_reported_as_complete(tmp_path: Path) -> None:
    site = FakeSite({("clasificaciones", GROUP, None): load("standings_incomplete_row.html")})
    rep = ingest_tables(None, "standings", inventory(), tmp_path, site, dry_run=True, now=lambda: NOW)
    g = rep["groups"][0]
    assert g["status"] == "incomplete" and any("descartada" in w or "5 equipos" in w for w in g["rounds"][0]["warnings"])


def test_every_configured_competition_and_group_is_visited_and_reported(tmp_path: Path) -> None:
    """Cobertura de la configuración: una entrada por competición del alcance (14) con dos grupos ficticios cada una."""
    entries, routes = [], {}
    for n, (_level, label) in enumerate(CATALOG_SCOPE):
        comp = f"{80000000 + n}"
        groups = [{"external_id": f"{comp}{i}", "name": f"Grupo {i}", "expected_rounds": 10, "expected_teams": 6} for i in (1, 2)]
        entries.append({"identification": "confirmed", "groups_status": "ok", "competition": {"external_id": comp, "name": label}, "groups": groups})
        for g in groups:
            page = load("standings_round4_full.html").replace(COMP, comp).replace(GROUP, str(g["external_id"]))
            routes[("clasificaciones", str(g["external_id"]), None)] = page
    inv = {"season": {"external_id": SEASON}, "game_type": "3", "entries": entries}
    site = FakeSite(routes)
    rep = ingest_tables(None, "standings", inv, tmp_path, site, dry_run=True, now=lambda: NOW)
    assert len(CATALOG_SCOPE) == 14 and len(rep["groups"]) == 28 and site.requests == 28
    assert all(g["status"] == "ok" for g in rep["groups"]) and len({g["competition_id"] for g in rep["groups"]}) == 14
    limited = ingest_tables(None, "standings", inv, tmp_path / "l", site, dry_run=True, limit_groups=3, now=lambda: NOW)
    one = ingest_tables(None, "standings", inv, tmp_path / "o", site, dry_run=True, only={"80000002"}, now=lambda: NOW)
    assert len(limited["groups"]) == 3 and {g["competition_id"] for g in one["groups"]} == {"80000002"}


@pytest.mark.skipif(not Path("data/rffm/catalog/s22_t3/inventory.json").exists(), reason="inventario del catálogo no descargado")
def test_the_real_inventory_feeds_both_ingestions_with_all_25_groups() -> None:
    import json

    from futsal.ingestion.rffm.catalog_ingest import iter_groups
    inv = json.loads(Path("data/rffm/catalog/s22_t3/inventory.json").read_text(encoding="utf-8"))
    pairs = iter_groups(inv)
    assert len(pairs) == 25 and len({c["external_id"] for c, _ in pairs}) == 14 and len({g["external_id"] for _, g in pairs}) == 25


def test_http_errors_are_retried_by_the_polite_fetcher_and_then_the_page_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from futsal.ingestion.rffm import client
    from futsal.ingestion.rffm.client import PoliteFetcher

    answers: list[object] = [httpx.HTTPStatusError("x", request=httpx.Request("GET", "https://www.rffm.es/x"),
                                                    response=httpx.Response(503)), load("standings_round4_full.html")]

    def fake_fetch(_url: str, timeout: float = 30.0) -> str:
        item = answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return str(item)

    monkeypatch.setattr(client, "fetch_html", fake_fetch)
    pauses: list[float] = []
    fetcher = PoliteFetcher(delay=0.0, sleep=pauses.append)
    got = fetch_table("standings", TARGET, fetcher, cache_path(tmp_path, COMP, GROUP, "standings"), now=lambda: NOW)
    assert got.ok and fetcher.requests == 2 and pauses                                              # un 503 transitorio se reintenta
    always_404 = PoliteFetcher(delay=0.0, sleep=pauses.append)
    monkeypatch.setattr(client, "fetch_html", lambda _u, timeout=30.0: (_ for _ in ()).throw(
        httpx.HTTPStatusError("x", request=httpx.Request("GET", "https://www.rffm.es/x"), response=httpx.Response(404))))
    bad = fetch_table("standings", TARGET, always_404, cache_path(tmp_path / "n", COMP, GROUP, "standings"), now=lambda: NOW)
    assert bad.error_code == "download" and "HTTP 404" in (bad.error or "") and always_404.requests == 1   # un 404 no se reintenta
