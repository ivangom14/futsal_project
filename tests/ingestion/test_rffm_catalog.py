import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from futsal.config.targets import CATALOG_SCOPE
from futsal.ingestion.rffm import client
from futsal.ingestion.rffm.catalog import (
    AMBIGUOUS,
    CONFIRMED,
    GROUPS_FAILED,
    GROUPS_OK,
    NO_GROUPS,
    NOT_FOUND,
    discover_catalog,
    normalize_name,
    parse_competitions,
    parse_groups,
    resolve_scope,
)
from futsal.ingestion.rffm.catalog_ingest import estimate_requests, plan_update, scrape_catalog
from futsal.ingestion.rffm.progress import Progress, fmt_duration
from futsal.ingestion.rffm.update_policy import UpdatePolicy, classify_round
from futsal.ingestion.rffm.client import FetchError, group_url, groups_url, round_url
from futsal.ingestion.rffm.league import Target
from futsal.ingestion.rffm.parser import RffmParseError

REF = Target("22", "100", "101", "3")
NOW = datetime(2026, 10, 17, tzinfo=UTC)

# Nombres tal y como los devuelve la web (espacios finales, «fase final- FS»), con IDs de prueba.
CATALOG_NAMES = {
    "TERCERA DIVISIÓN FS": "1", "COPA SENIOR I - fase final- FS ": "2", "COPA SENIOR I - fase previa- FS ": "3",
    "PRIMERA DIVISION AUTONOMICA AFICIONADO FS": "4", "PREFERENTE AFICIONADO FS": "5",
    "COPA SENIOR II - fase final- FS ": "6", "COPA SENIOR II - fase previa- FS ": "7", "PRIMERA AFICIONADO FS ": "8",
    "TROFEO COMUNIDAD DIVISION HONOR JUVENIL FS ": "9", "PRIMERA DIVISION AUTONOMICA JUVENIL FS": "10",
    "PREFERENTE JUVENIL FS": "11", "COPA JUVENIL - fase final- FS ": "12", "COPA JUVENIL - fase previa- FS ": "13",
    "PRIMERA JUVENIL FS ": "14",
    # distractores: parecidos pero distintos
    "COPA JUVENIL FEMENINO - Fase de grupos- FS ": "90", "JUVENIL FEMENINO FS ": "91",
    "PRIMERA DIVISION AUTONOMICA CADETE FS ": "92", "TROFEO COMUNIDAD SENIOR MASCULINO FS ": "93",
}


@pytest.fixture(autouse=True)
def no_internet(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("acceso a red en test unitario")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(client.httpx, "get", boom)


def comps(names: dict[str, str] = CATALOG_NAMES, game_type: str = "3") -> list[dict[str, str]]:
    return [{"codigo": cid, "nombre": n, "codigo_tipo_juego": game_type, "NombreCategoria": "CAT",
             "CodigoCategoria": "5", "Activa": "1", "FechaInicio": "2026-09-01", "FechaFin": "2027-05-01"}
            for n, cid in names.items()]


def parsed(competitions: list[dict[str, str]]) -> list[dict[str, str | None]]:
    return parse_competitions(catalog_html(competitions))[1]


def html(props: dict[str, Any]) -> str:
    data = json.dumps({"props": {"pageProps": props}})
    return f'<script id="__NEXT_DATA__" type="application/json">{data}</script>'


def catalog_html(competitions: list[dict[str, str]]) -> str:
    return html({"season": {"cod_temporada": "22", "nombre": "2026-2027"}, "competitions": competitions})


def match(mid: str, home: str, away: str, hid: str, aid: str, played: bool) -> dict[str, str]:
    return {"codacta": mid, "CodEquipo_local": hid, "CodEquipo_visitante": aid, "Nombre_equipo_local": home,
            "Nombre_equipo_visitante": away, "Goles_casa": "2" if played else "", "Goles_visitante": "1" if played else "",
            "fecha": "03/10/2026" if played else "01/12/2026", "hora": "10:00", "campojuego": "C", "codigo_campo": "9",
            "estado": "1", "situacion_juego": "1", "motivo_estado": "", "acta_cerrada": "1" if played else "0"}


def round_page(comp: str, group: str, rid: str, rounds: list[str], partidos: list[dict[str, str]]) -> str:
    return html({
        "query": {"jornada": rid}, "idGameType": 3, "season": {"cod_temporada": "22"},
        "rounds": {"jornadas": [{"codjornada": r, "nombre": r, "fecha_jornada": "26-09-2026"} for r in rounds]},
        "results": {"codigo_competicion": comp, "codigo_grupo": group, "jornada": rid, "nombre_jornada": rid,
                    "nombre_competicion": f"COMP{comp}", "nombre_grupo": f"G{group}", "fecha_jornada": "03/10/2026",
                    "partidos": partidos}})


class FakeWeb:
    """Sirve catálogo, grupos y jornadas; `fail` lista URLs que fallan (una vez si `once`)."""

    def __init__(self, groups: dict[str, Any], pages: dict[str, str], competitions: list[dict[str, str]] | None = None):
        self.map: dict[str, Any] = {group_url(REF.season, REF.competition, REF.group, "3"):
                                    catalog_html(competitions or comps())}
        for comp, g in groups.items():
            self.map[groups_url(comp)] = g
        self.map.update(pages)
        self.calls: list[str] = []
        self.fail: set[str] = set()

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if url in self.fail:
            raise FetchError("HTTP 503")
        value = self.map[url]
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps(value)

    @property
    def requests(self) -> int:
        return len(self.calls)


def group_json(*gids: str, rounds: str = "2", teams: str = "4") -> list[dict[str, str]]:
    return [{"codigo": g, "nombre": f"Grupo {g}", "total_jornadas": rounds, "total_equipos": teams} for g in gids]


def two_group_web() -> FakeWeb:
    """Dos grupos de la misma competición con equipos de igual nombre (IDs distintos) y 2 jornadas."""
    pages: dict[str, str] = {}
    for gid, offset in (("201", "1"), ("202", "2")):
        t = [offset + str(i) for i in range(4)]
        rounds = ["11", "12"]
        ms = {"11": [match(f"m{gid}a", "Madrid FS", "Getafe FS", t[0], t[1], True),
                     match(f"m{gid}b", "Leganés", "Alcorcón", t[2], t[3], True)],
              "12": [match(f"m{gid}c", "Getafe FS", "Leganés", t[1], t[2], False),
                     match(f"m{gid}d", "Alcorcón", "Madrid FS", t[3], t[0], False)]}
        pages[group_url("22", "5", gid, "3")] = round_page("5", gid, "11", rounds, ms["11"])
        for rid in rounds:
            pages[round_url("22", "5", gid, rid, "3")] = round_page("5", gid, rid, rounds, ms[rid])
    return FakeWeb({"5": group_json("201", "202")}, pages)


def inventory(web: FakeWeb, tmp_path: Path) -> dict[str, Any]:
    scope = (("senior", "Primera Aficionado FS"),)
    names = {"PRIMERA AFICIONADO FS ": "5"}
    web.map[group_url("22", "100", "101", "3")] = catalog_html(comps(names))
    return discover_catalog(REF, scope, web, tmp_path / "cat", now=lambda: NOW)


# --- nomenclatura y resolución -------------------------------------------------------------

def test_normalize_name_ignores_case_accents_and_punctuation() -> None:
    assert normalize_name("COPA SENIOR I - fase final- FS ") == normalize_name("Copa Senior I - fase final - FS")
    assert normalize_name("Tercera División FS") == normalize_name("TERCERA DIVISION FS")
    assert normalize_name("Primera Juvenil FS") != normalize_name("Primera División Autonómica Juvenil FS")


def test_fourteen_entries_resolve_without_mixing_competitions() -> None:
    entries = resolve_scope(parsed(comps()), CATALOG_SCOPE, "3")
    assert [e["identification"] for e in entries] == [CONFIRMED] * 14
    assert [e["competition"]["external_id"] for e in entries] == [str(i) for i in range(1, 15)]
    assert [e["level"] for e in entries] == ["senior"] * 8 + ["juvenil"] * 6


def test_ambiguous_not_found_and_wrong_game_type() -> None:
    dup = comps({"PREFERENTE JUVENIL FS": "11", "PREFERENTE JUVENIL FS ": "99"})
    wrong = comps({"TERCERA DIVISIÓN FS": "1"}, game_type="1")
    by_label = {e["requested_label"]: e for e in resolve_scope(parsed(dup + wrong), CATALOG_SCOPE, "3")}
    assert by_label["Preferente Juvenil FS"]["identification"] == AMBIGUOUS
    assert len(by_label["Preferente Juvenil FS"]["candidates"]) == 2
    assert by_label["Preferente Juvenil FS"]["competition"] is None
    assert by_label["Tercera División FS"]["identification"] == NOT_FOUND  # solo existe en otro tipo de juego
    assert by_label["Primera Juvenil FS"]["identification"] == NOT_FOUND  # no se sustituye por una parecida


def test_parse_groups_dedups_and_rejects_bad_payload() -> None:
    groups = parse_groups(group_json("7", "7", "8") + [{"nombre": "sin código"}, "x"])
    assert [g["external_id"] for g in groups] == ["7", "8"]
    assert groups[0]["expected_rounds"] == 2 and groups[0]["expected_teams"] == 4
    with pytest.raises(RffmParseError):
        parse_groups({"error": "no es lista"})


# --- descubrimiento de grupos -----------------------------------------------------------------

def test_discovery_distinguishes_groups_none_and_failures(tmp_path: Path) -> None:
    scope = (("senior", "Tercera División FS"), ("senior", "Preferente Aficionado FS"),
             ("juvenil", "Primera Juvenil FS"), ("juvenil", "Preferente Juvenil FS"))
    web = FakeWeb({"1": group_json("a", "b"), "5": [], "14": FetchError("HTTP 503"), "11": "no es json"}, {})
    inv = discover_catalog(REF, scope, web, tmp_path, now=lambda: NOW)
    status = {e["requested_label"]: (e["identification"], e["groups_status"]) for e in inv["entries"]}
    assert status["Tercera División FS"] == (CONFIRMED, GROUPS_OK)
    assert status["Preferente Aficionado FS"] == (CONFIRMED, NO_GROUPS)
    assert status["Primera Juvenil FS"] == (CONFIRMED, GROUPS_FAILED)
    assert status["Preferente Juvenil FS"] == (CONFIRMED, GROUPS_FAILED)
    s = inv["summary"]
    assert (s["groups_total"], s["competitions_without_groups"], s["group_requests_failed"]) == (2, 1, 2)
    assert not (tmp_path / "groups" / "11.json").exists()  # una respuesta inválida no se cachea
    assert not (tmp_path / "groups" / "14.json").exists()


def test_discovery_reuses_cache_and_refreshes(tmp_path: Path) -> None:
    scope = (("senior", "Tercera División FS"),)
    web = FakeWeb({"1": group_json("a")}, {})
    discover_catalog(REF, scope, web, tmp_path)
    assert web.requests == 2  # catálogo + grupos
    again = discover_catalog(REF, scope, web, tmp_path)
    assert web.requests == 2 and again["summary"]["groups_from_cache"] == 1
    discover_catalog(REF, scope, web, tmp_path, refresh=True)
    assert web.requests == 4


def test_catalog_unavailable_is_an_error(tmp_path: Path) -> None:
    web = FakeWeb({}, {})
    web.fail.add(group_url("22", "100", "101", "3"))
    with pytest.raises(RuntimeError, match="catálogo"):
        discover_catalog(REF, CATALOG_SCOPE, web, tmp_path)


# --- ingesta de grupos -----------------------------------------------------------------------

def test_scrape_all_groups_keeps_similar_team_names_apart(tmp_path: Path) -> None:
    web = two_group_web()
    inv = inventory(web, tmp_path)
    rep = scrape_catalog(inv, tmp_path / "out", web, now=lambda: NOW, requests_made=lambda: web.requests)
    assert [g["status"] for g in rep["groups"]] == ["downloaded", "downloaded"]
    assert all(g["matches_total"] == 4 and g["rounds_discovered"] == 2 for g in rep["groups"])
    leagues = [json.loads(Path(g["league_path"]).read_text()) for g in rep["groups"]]
    ids = [{t for m in lg["matches"] for t in (m["home_team_external_id"], m["away_team_external_id"])} for lg in leagues]
    assert ids[0].isdisjoint(ids[1])  # mismos nombres, IDs de equipo distintos por grupo
    assert {lg["group"]["external_id"] for lg in leagues} == {"201", "202"}
    assert rep["groups"][0]["teams_in_matches"] == 4 and rep["groups"][0]["teams_match_expected"] is True


def test_repeating_scrape_makes_no_requests_and_same_content(tmp_path: Path) -> None:
    web = two_group_web()
    inv = inventory(web, tmp_path)
    scrape_catalog(inv, tmp_path / "out", web, requests_made=lambda: web.requests)
    first = [p.read_text() for p in sorted((tmp_path / "out").glob("c*/g*/league.json"))]
    before = web.requests
    rep = scrape_catalog(inv, tmp_path / "out", web, resume=True, requests_made=lambda: web.requests)
    assert web.requests == before and all(g["http_requests"] == 0 for g in rep["groups"])
    second = [p.read_text() for p in sorted((tmp_path / "out").glob("c*/g*/league.json"))]
    assert [json.loads(a)["matches"] for a in first] == [json.loads(b)["matches"] for b in second]
    assert all(len({m["external_match_id"] for m in json.loads(a)["matches"]}) == 4 for a in first)


def test_interruption_is_partial_then_resume_downloads_only_missing(tmp_path: Path) -> None:
    web = two_group_web()
    inv = inventory(web, tmp_path)
    web.fail.add(round_url("22", "5", "201", "12", "3"))
    rep = scrape_catalog(inv, tmp_path / "out", web, requests_made=lambda: web.requests)
    g201, g202 = rep["groups"]
    assert g201["status"] == "partial" and g201["rounds_failed"] == 1 and "12" in g201["failed_rounds"]
    assert g202["status"] == "downloaded"  # un error aislado no detiene los demás grupos
    saved = json.loads((tmp_path / "out" / "catalog-scrape-report.json").read_text())
    assert len(saved["groups"]) == 2
    web.fail.clear()
    web.calls.clear()
    rep2 = scrape_catalog(inv, tmp_path / "out", web, resume=True, requests_made=lambda: web.requests)
    assert [g["status"] for g in rep2["groups"]] == ["downloaded", "downloaded"]
    assert web.calls == [round_url("22", "5", "201", "12", "3")]  # solo lo que faltaba


def test_group_failures_are_isolated_and_classified(tmp_path: Path) -> None:
    web = two_group_web()
    inv = inventory(web, tmp_path)
    web.map[group_url("22", "5", "201", "3")] = html({"results": {}, "rounds": {"jornadas": []}})  # sin jornadas
    web.map[group_url("22", "5", "202", "3")] = FetchError("HTTP 503")
    web.map[groups_url("5")] = group_json("201", "202", "203")
    inv = discover_catalog(REF, (("senior", "Primera Aficionado FS"),), web, tmp_path / "cat2", refresh=True)
    web.map[group_url("22", "5", "203", "3")] = round_page("9", "203", "1", ["1"], [])  # competición distinta
    rep = scrape_catalog(inv, tmp_path / "out", web, requests_made=lambda: web.requests)
    assert [g["status"] for g in rep["groups"]] == ["no_rounds", "failed", "failed"]
    assert "identidad" in rep["groups"][2]["error"] or "inesperada" in rep["groups"][2]["error"]
    assert not any((tmp_path / "out").glob("c*/g*/league.json"))  # no se escribe nada como si estuviera completo


def test_estimate_counts_cached_rounds(tmp_path: Path) -> None:
    web = two_group_web()
    inv = inventory(web, tmp_path)
    assert estimate_requests(inv, tmp_path / "out") == {"groups": 2, "estimated_requests": 6,
                                                         "groups_without_expected_rounds": 0}
    scrape_catalog(inv, tmp_path / "out", web, requests_made=lambda: web.requests)
    assert estimate_requests(inv, tmp_path / "out")["estimated_requests"] == 0
    assert estimate_requests(inv, tmp_path / "out", only={"nope"})["groups"] == 0


# --- contador de progreso ------------------------------------------------------------------------------------------

def test_progress_shows_position_percentage_and_remaining_time(tmp_path: Path) -> None:
    import io

    web = two_group_web()
    inv = inventory(web, tmp_path)
    plan = estimate_requests(inv, tmp_path / "out")
    now = [100.0]

    class Clock:
        def __call__(self) -> float:
            return now[0]

    out = io.StringIO()
    progress = Progress(plan["estimated_requests"], plan["groups"], lambda: web.requests, clock=Clock(), out=out, live=False)
    base = web.requests

    def ticking(url: str) -> str:
        now[0] += 2.0  # cada petición tarda 2 s
        return web(url)

    ticking.requests = 0  # type: ignore[attr-defined]
    rep = scrape_catalog(inv, tmp_path / "out", ticking, requests_made=lambda: web.requests, progress=progress)
    progress.finish()
    text = out.getvalue()
    assert [g["status"] for g in rep["groups"]] == ["downloaded", "downloaded"]            # el contador no cambia el resultado
    assert "[grupo 1/2]" in text and "[grupo 2/2]" in text and "jornada 2/2" in text
    assert f"peticiones {web.requests - base}/~{plan['estimated_requests']}" in text
    assert "restante ~" in text and "transcurrido" in text and "Descarga terminada" in text


def test_progress_eta_and_formats() -> None:
    assert fmt_duration(59.6) == "01:00" and fmt_duration(3725) == "1:02:05" and fmt_duration(-3) == "00:00"
    now = [0.0]
    done = [0]
    p = Progress(100, 2, lambda: done[0], clock=lambda: now[0], out=__import__("io").StringIO(), live=False)
    assert p.remaining_seconds() is None and "restante n/d" in p.line()                  # sin datos no se inventa un ETA
    done[0], now[0] = 25, 50.0                                                           # 25 de 100 en 50 s: quedan 75 → 150 s
    assert p.remaining_seconds() == 150.0 and "peticiones 25/~100 (25 %)" in p.line() and "restante ~02:30" in p.line()
    done[0] = 130                                                                        # más peticiones que las previstas: tope 100 %
    assert "(100 %)" in p.line() and p.remaining_seconds() == 0.0
    counter = [0]
    nothing = Progress(0, 1, lambda: counter[0], out=__import__("io").StringIO(), live=False)
    counter[0] = 3
    assert "peticiones 3" in nothing.line() and "%" not in nothing.line()


def test_progress_live_mode_rewrites_one_line_and_cached_groups_need_no_requests(tmp_path: Path) -> None:
    import io

    web = two_group_web()
    inv = inventory(web, tmp_path)
    scrape_catalog(inv, tmp_path / "out", web, requests_made=lambda: web.requests)
    plan = estimate_requests(inv, tmp_path / "out")
    assert plan["estimated_requests"] == 0                                               # todo en caché: nada que pedir
    out = io.StringIO()
    progress = Progress(plan["estimated_requests"], plan["groups"], lambda: web.requests, out=out, live=True)
    before = web.requests
    scrape_catalog(inv, tmp_path / "out", web, requests_made=lambda: web.requests, progress=progress)
    progress.finish()
    assert web.requests == before and "\r" in out.getvalue() and out.getvalue().count("\n") >= 3   # un salto por grupo y el final


# --- scrape-catalog --update: solo jornadas con partidos sin resultado, aplazados o con errores -----------------------

UPD_NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def age_snapshots(root: Path, days: float) -> None:
    """Fija la fecha de los snapshots (el 'tomado hace N días' es lo que mira la política)."""
    import os

    stamp = (UPD_NOW - timedelta(days=days)).timestamp()
    for f in root.rglob("*.html"):
        os.utime(f, (stamp, stamp))


def downloaded(tmp_path: Path, web: FakeWeb | None = None) -> tuple[FakeWeb, dict[str, Any], Path]:
    web = web or two_group_web()
    inv = inventory(web, tmp_path)
    out = tmp_path / "out"
    scrape_catalog(inv, out, web, requests_made=lambda: web.requests, now=lambda: UPD_NOW)
    return web, inv, out


def run_update(web: FakeWeb, inv: dict[str, Any], out: Path, policy: UpdatePolicy, at: datetime = UPD_NOW) -> dict[str, Any]:
    return scrape_catalog(inv, out, web, requests_made=lambda: web.requests, now=lambda: at, update=policy)


def test_update_skips_closed_and_far_rounds_and_only_refreshes_the_seed(tmp_path: Path) -> None:
    web, inv, out = downloaded(tmp_path)
    age_snapshots(out, 5)                                         # ronda 11 jugada el 03/10; ronda 12 programada el 01/12
    before = web.requests
    rep = run_update(web, inv, out, UpdatePolicy())
    assert web.requests - before == 2                              # solo la semilla de cada grupo (detecta jornadas nuevas)
    assert [g["status"] for g in rep["groups"]] == ["downloaded", "downloaded"]
    assert all(g["update"]["refetched"] == {} and g["update"]["skipped_closed"] == 2 for g in rep["groups"])


def test_update_refetches_pending_rounds_within_horizon_and_picks_up_new_results(tmp_path: Path) -> None:
    web, inv, out = downloaded(tmp_path)
    age_snapshots(out, 5)
    for gid, offset in (("201", "1"), ("202", "2")):                # la ronda 12 ya se ha jugado en la web
        t = [offset + str(i) for i in range(4)]
        web.map[round_url("22", "5", gid, "12", "3")] = round_page("5", gid, "12", ["11", "12"], [
            match(f"m{gid}c", "Getafe FS", "Leganés", t[1], t[2], True), match(f"m{gid}d", "Alcorcón", "Madrid FS", t[3], t[0], True)])
    before = web.requests
    rep = run_update(web, inv, out, UpdatePolicy(horizon_days=90))
    assert web.requests - before == 4                              # 2 semillas + la ronda 12 de cada grupo
    for g in rep["groups"]:
        assert g["update"]["refetched"] == {"12": "pendiente_proximo"} and g["matches_finished"] == 4 and g["matches_scheduled"] == 0
    league = json.loads(Path(rep["groups"][0]["league_path"]).read_text())
    assert {m["status"] for m in league["matches"]} == {"finished"}


def test_update_rechecks_postponed_and_first_pass_errors_regardless_of_date(tmp_path: Path) -> None:
    web = two_group_web()
    bad = match("m201c", "Getafe FS", "Leganés", "12", "13", False)
    bad["motivo_estado"] = "Aplazado"
    web.map[round_url("22", "5", "201", "12", "3")] = round_page("5", "201", "12", ["11", "12"], [bad, match("m201d", "Alcorcón", "Madrid FS", "13", "10", False)])
    broken = match("m202a", "Madrid FS", "Getafe FS", "20", "21", True)
    broken["Goles_visitante"] = ""                                 # marcador a medias en la primera pasada
    page = round_page("5", "202", "11", ["11", "12"], [broken, match("m202b", "Leganés", "Alcorcón", "22", "23", True)])
    web.map[round_url("22", "5", "202", "11", "3")] = page
    web.map[group_url("22", "5", "202", "3")] = page                 # la semilla es la jornada 11: también viene a medias
    web, inv, out = downloaded(tmp_path, web)
    age_snapshots(out, 5)
    rep = run_update(web, inv, out, UpdatePolicy())                # horizonte por defecto: la ronda 12 lejana se omitiría...
    first, second = rep["groups"][0]["update"], rep["groups"][1]["update"]
    assert first["refetched"] == {"12": "aplazado_o_suspendido"}   # ...pero está aplazada: se revisa siempre
    assert second["refetched"]["11"].startswith("anomalia:") and "partial_score" in second["refetched"]["11"]


def test_update_keeps_the_old_snapshot_when_the_refresh_fails(tmp_path: Path) -> None:
    web, inv, out = downloaded(tmp_path)
    age_snapshots(out, 5)
    web.fail.add(round_url("22", "5", "201", "12", "3"))
    rep = run_update(web, inv, out, UpdatePolicy(horizon_days=90))
    g = rep["groups"][0]
    assert g["status"] == "downloaded" and g["rounds_failed"] == 0 and list(g["update"]["kept_stale"]) == ["12"]
    assert g["matches_total"] == 4                                  # la jornada no se pierde: se conserva el snapshot anterior
    assert rep["groups"][1]["update"]["refetched"] == {"12": "pendiente_proximo"}
    assert g["quality_issues"].get("refetch_failed_kept_snapshot") == 1


def test_update_does_not_hammer_recent_snapshots_and_fetches_missing_rounds(tmp_path: Path) -> None:
    web, inv, out = downloaded(tmp_path)
    before = web.requests
    run_update(web, inv, out, UpdatePolicy(horizon_days=90, min_age_hours=12))   # snapshots recién descargados (mtime real)
    assert web.requests == before                                  # ni semilla ni jornadas: demasiado recientes
    (out / "c5" / "g201" / "rounds" / "12.html").unlink()          # una jornada fallida/ausente se pide siempre
    rep = run_update(web, inv, out, UpdatePolicy(horizon_days=90, min_age_hours=12))
    assert web.requests - before == 1 and rep["groups"][0]["matches_total"] == 4


def test_update_plan_matches_what_update_requests_and_never_touches_the_web(tmp_path: Path) -> None:
    web, inv, out = downloaded(tmp_path)
    age_snapshots(out, 5)
    policy = UpdatePolicy(horizon_days=90)
    calls = web.requests
    plan = plan_update(inv, out, policy, UPD_NOW)
    assert web.requests == calls                                    # el plan no usa la red
    assert plan["by_reason"] == {"pendiente_proximo": 2} and plan["rounds_skipped_closed"] == 2 and plan["estimated_requests"] == 4
    run_update(web, inv, out, policy)
    assert web.requests - calls == plan["estimated_requests"]


def test_classify_round_reasons() -> None:
    from futsal.ingestion.rffm.models import RoundData

    def rd(status: str, day: str, hs: int | None = 2, aw: int | None = 1, date_: str | None = "2026-10-03") -> RoundData:
        m = {"external_match_id": "m1", "home_team_name": "A", "home_team_external_id": "1", "away_team_name": "B",
             "away_team_external_id": "2", "home_score": hs, "away_score": aw, "status": status, "status_raw": None,
             "scheduled_date": date_, "scheduled_time": None, "timezone": "Europe/Madrid", "venue": None,
             "venue_external_id": None, "match_report_url": None, "comparison_url": None, "source_url": "u"}
        return RoundData.model_validate({"external_round_id": "1", "round_number_or_label": "1", "round_date": day,
                                         "season_external_id": "22", "competition_external_id": "5", "competition_name": None,
                                         "group_external_id": "9", "group_name": None, "source_url": "u",
                                         "observed_at": UPD_NOW, "matches": [m]})

    pol = UpdatePolicy()
    old = UPD_NOW - timedelta(days=5)
    assert classify_round(rd("finished", "2026-10-03"), None, old, UPD_NOW, pol) is None
    assert classify_round(rd("finished", "2026-10-03"), None, UPD_NOW - timedelta(hours=2), UPD_NOW, pol) is None   # demasiado reciente
    assert classify_round(rd("finished", "2026-10-16", date_="2026-10-16"), None, UPD_NOW - timedelta(days=1), UPD_NOW, pol) == "cerrada_reciente"
    assert classify_round(rd("postponed", "2026-10-03", None, None), None, old, UPD_NOW, pol) == "aplazado_o_suspendido"
    assert classify_round(rd("scheduled", "2026-10-20", None, None, "2026-10-20"), None, old, UPD_NOW, pol) == "pendiente_proximo"
    assert classify_round(rd("scheduled", "2026-12-01", None, None, "2026-12-01"), None, old, UPD_NOW, pol) is None
    assert classify_round(rd("scheduled", "2026-12-01", None, None, "2026-12-01"), None, UPD_NOW - timedelta(days=40), UPD_NOW, pol) == "lejano_caducado"
    assert classify_round(rd("scheduled", "2026-10-03", None, None, None), None, UPD_NOW - timedelta(days=40), UPD_NOW, pol) == "sin_fecha"


def test_cli_update_flags_plan_offline_and_incompatible_with_refresh(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from futsal import cli

    web = two_group_web()
    inv = inventory(web, tmp_path / "cat")
    work = tmp_path / "work" / "s22_t3"
    work.mkdir(parents=True)
    (work / "inventory.json").write_text(json.dumps(inv), encoding="utf-8")
    base = ["scrape-catalog", "--output", str(tmp_path / "work")]
    assert cli.main([*base, "--update", "--refresh"]) == 2
    assert "incompatibles" in capsys.readouterr().out
    assert cli.main([*base, "--update", "--plan"]) == 0                   # sin semillas locales: no hay red ni descarga
    plan = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert plan["groups"] == 2 and plan["groups_without_seed"] == 2 and "sin_semilla" in " ".join(plan["by_reason"])
    assert cli.main([*base, "--update", "--plan", "--horizon-days", "30", "--min-age-hours", "1"]) == 0

