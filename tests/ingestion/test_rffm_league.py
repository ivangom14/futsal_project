import json
import socket
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from futsal.cli import main
from futsal.ingestion.rffm import client
from futsal.ingestion.rffm.client import FetchError, PoliteFetcher
from futsal.ingestion.rffm.league import Target, discover_rounds, scrape_league
from futsal.ingestion.rffm.parser import RffmParseError

T = Target("22", "26738243", "26738245", "3")
NOW = datetime(2026, 10, 4, tzinfo=UTC)
LABELS = {"10": "1", "20": "2", "30": "3", "40": "4"}  # id técnico -> etiqueta visible


@pytest.fixture(autouse=True)
def no_internet(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("acceso a red en test unitario")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(client.httpx, "get", boom)


def match(mid: str, home: str = "A", away: str = "B", gh: str = "1", ga: str = "0",
          fecha: str = "03/10/2026", closed: str = "1") -> dict[str, str]:
    return {"codacta": mid, "CodEquipo_local": "1" + home, "CodEquipo_visitante": "2" + away,
            "Nombre_equipo_local": home, "Nombre_equipo_visitante": away, "Goles_casa": gh,
            "Goles_visitante": ga, "fecha": fecha, "hora": "10:00", "campojuego": "C",
            "codigo_campo": "9", "estado": "1", "situacion_juego": "1", "motivo_estado": "",
            "acta_cerrada": closed}


def future(mid: str) -> dict[str, str]:
    return match(mid, gh="", ga="", fecha="01/12/2026", closed="0")


def page(rid: str, partidos: list[dict[str, str]], comp: str = "26738243",
         group: str = "26738245", season: str = "22", labels: dict[str, str] = LABELS) -> str:
    jornadas = [{"codjornada": k, "nombre": v, "fecha_jornada": "26-09-2026"} for k, v in labels.items()]
    jornadas.append(dict(jornadas[0]))  # duplicada a propósito
    props = {
        "query": {"jornada": rid}, "idGameType": 3, "season": {"cod_temporada": season},
        "rounds": {"jornadas": jornadas},
        "results": {"codigo_competicion": comp, "codigo_grupo": group, "jornada": rid,
                    "nombre_jornada": labels.get(rid, rid), "nombre_competicion": "COMP",
                    "nombre_grupo": "G", "fecha_jornada": "03/10/2026", "partidos": partidos},
    }
    data = json.dumps({"props": {"pageProps": props}})
    return f'<html><script id="__NEXT_DATA__" type="application/json">{data}</script></html>'


class Fake:
    def __init__(self, pages: dict[str, Any]) -> None:
        self.pages, self.calls = pages, []

    def __call__(self, url: str) -> str:
        rid = url.split("jornada=")[1].split("&")[0]
        self.calls.append(rid)
        value = self.pages[rid]
        if isinstance(value, Exception):
            raise value
        return str(value)


def pages() -> dict[str, Any]:
    return {"10": page("10", [match("m1"), match("m2", "C", "D")]),
            "20": page("20", [match("m3", "E", "F")]),
            "30": page("30", [future("m4"), future("m5")]),
            "40": page("40", [])}


def run(tmp: Path, fake: Fake, **kw: Any) -> dict[str, Any]:
    return scrape_league(T, tmp, fake, "20", now=lambda: NOW, **kw)


def test_discovery_separates_technical_id_from_label_and_dedupes() -> None:
    refs, issues = discover_rounds(pages()["10"], T)
    assert [r.external_round_id for r in refs] == ["10", "20", "30", "40"]
    assert [r.visible_round_label for r in refs] == ["1", "2", "3", "4"]
    assert refs[1].visible_round_number == 2 and refs[1].scheduled_date == date(2026, 9, 26)
    assert [i["code"] for i in issues] == ["duplicate_round"]
    assert all(r.source_url.count("jornada=") == 1 for r in refs)


def test_urls_built_per_round() -> None:
    refs, _ = discover_rounds(pages()["10"], T)
    assert refs[2].source_url == (
        "https://www.rffm.es/competicion/resultados-y-jornadas?temporada=22&competicion=26738243"
        "&grupo=26738245&jornada=30&tipojuego=3")


@pytest.mark.parametrize("bad", [{"comp": "1"}, {"group": "1"}, {"season": "21"}])
def test_strict_identity_rejects_other_competition(tmp_path: Path, bad: dict[str, str]) -> None:
    p = pages()
    p["30"] = page("30", [match("x1")], **bad)
    s = run(tmp_path, Fake(p))
    assert s["rounds_failed"] == 1 and "30" in s["failed_rounds"]
    assert not (tmp_path / "rounds" / "30.html").exists()
    assert "x1" not in (tmp_path / "league.json").read_text()


def test_aggregation_future_and_empty_rounds(tmp_path: Path) -> None:
    s = run(tmp_path, Fake(pages()))
    assert (s["rounds_discovered"], s["rounds_downloaded"], s["http_requests"] == 0) == (4, 4, True)
    assert (s["matches_total"], s["matches_finished"], s["matches_scheduled"]) == (5, 3, 2)
    league = json.loads((tmp_path / "league.json").read_text())
    sched = [m for m in league["matches"] if m["status"] == "scheduled"]
    assert all(m["home_score"] is None and m["away_score"] is None for m in sched)
    assert {i["code"] for i in s["quality_issues"]} == {"duplicate_round", "empty_round"}


def test_duplicate_matches_are_reported(tmp_path: Path) -> None:
    p = pages()
    p["30"] = page("30", [match("m9"), match("m9")])
    p["40"] = page("40", [match("m1")])
    s = run(tmp_path, Fake(p))
    codes = [i["code"] for i in s["quality_issues"]]
    assert s["duplicate_match_ids"] == ["m1", "m9"]
    assert "match_in_several_rounds" in codes and "duplicate_match" in codes
    assert s["matches_total"] == 4


def test_match_level_anomalies(tmp_path: Path) -> None:
    p = pages()
    p["30"] = page("30", [match("a", "X", "X"), match("b", gh="2", ga=""), match("c", gh="x"),
                          match("d", "", "Z"), match("e", gh="", ga="", closed="1"),
                          match("f", fecha="01/12/2026", gh="1", ga="1", closed="0")])
    codes = {i["code"] for i in run(tmp_path, Fake(p))["quality_issues"]}
    assert {"same_team", "partial_score", "non_numeric_score", "empty_team",
            "finished_without_score", "future_match_with_score"} <= codes


def test_isolated_failure_and_resume(tmp_path: Path) -> None:
    p = pages()
    p["30"] = FetchError("HTTP 500")
    first = Fake(p)
    s = run(tmp_path, first)
    assert s["rounds_failed"] == 1 and s["matches_total"] == 3
    assert (tmp_path / "rounds" / "10.json").exists() and not (tmp_path / "rounds" / "30.json").exists()
    p["30"] = page("30", [future("m4")])
    second = Fake(p)
    s2 = run(tmp_path, second, resume=True)
    assert second.calls == ["30"] and s2["rounds_failed"] == 0
    assert s2["rounds_from_cache"] == 3 and s2["matches_total"] == 4


def test_cache_needs_no_network_and_is_deterministic(tmp_path: Path) -> None:
    run(tmp_path, Fake(pages()))
    first = json.loads((tmp_path / "league.json").read_text())
    offline = Fake({})
    s = run(tmp_path, offline)
    assert offline.calls == [] and s["rounds_from_cache"] == 4 and s["rounds_downloaded"] == 0
    again = json.loads((tmp_path / "league.json").read_text())
    for d in (first, again):
        d.pop("generated_at")
    assert first == again


def test_max_rounds_limits_requests(tmp_path: Path) -> None:
    fake = Fake(pages())
    s = run(tmp_path, fake, max_rounds=2, refresh=True)
    assert fake.calls == ["20", "10"] and s["rounds_selected"] == 2 and s["rounds_discovered"] == 4


def test_discovery_without_rounds_fails() -> None:
    with pytest.raises(RffmParseError):
        discover_rounds('<script id="__NEXT_DATA__">{"props":{"pageProps":{}}}</script>', T)


def test_polite_fetcher_retries_only_transient(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    sleeps: list[float] = []
    seq = [429, 200]

    def fake_get(url: str, **k: Any) -> httpx.Response:
        code = seq.pop(0)
        return httpx.Response(code, text="ok", headers={"Retry-After": "7"},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(client.httpx, "get", fake_get)
    f = PoliteFetcher(delay=1, sleep=sleeps.append)
    assert f("https://www.rffm.es/x") == "ok" and f.requests == 2 and 7.0 in sleeps
    seq[:] = [404]
    with pytest.raises(FetchError):
        f("https://www.rffm.es/x")
    assert f.requests == 3


def test_cli_list_rounds_uses_cache(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "rounds").mkdir()
    (tmp_path / "rounds" / "2.html").write_text(pages()["10"], encoding="utf-8")
    assert main(["list-rounds", "--output", str(tmp_path)]) == 0
    assert "4 jornadas" in capsys.readouterr().out


def test_delay_applies_between_real_requests_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = pages()

    def fake_fetch(url: str, timeout: float = 30.0) -> str:
        return str(html[url.split("jornada=")[1].split("&")[0]])

    monkeypatch.setattr(client, "fetch_html", fake_fetch)
    sleeps: list[float] = []
    fetcher = PoliteFetcher(delay=2.0, sleep=sleeps.append)
    s = scrape_league(T, tmp_path, fetcher, "20", now=lambda: NOW, requests_made=lambda: fetcher.requests)
    # N peticiones reales -> N-1 pausas de `delay`; sin esperas reales (sleep simulado)
    assert s["http_requests"] == fetcher.requests > 1
    assert sleeps == [2.0] * (fetcher.requests - 1)
    sleeps.clear()
    before = fetcher.requests
    scrape_league(T, tmp_path, fetcher, "20", now=lambda: NOW, requests_made=lambda: fetcher.requests)
    assert fetcher.requests == before and sleeps == []  # lecturas de caché: ni petición ni pausa
