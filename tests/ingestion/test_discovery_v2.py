import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from futsal.cli import main
from futsal.ingestion.rffm import client
from futsal.ingestion.rffm.catalog_ingest import REPORT_NAME
from futsal.ingestion.rffm.client import FetchError, competitions_api_url, groups_url
from futsal.ingestion.rffm.discovery_v2 import diff, discover, fetch_from_dir, plan_requests

CAT = Path(__file__).parents[1] / "fixtures" / "rffm_catalog"
COMPS: list[dict[str, Any]] = json.loads((CAT / "api_competitions_s22_t3.json").read_text(encoding="utf-8"))
GROUPS: dict[str, list[dict[str, Any]]] = json.loads((CAT / "api_groups_sample.json").read_text(encoding="utf-8"))


def make_fetch(comps: Any = COMPS, groups: dict[str, Any] | None = None, log: list[str] | None = None):  # type: ignore[no-untyped-def]
    table = GROUPS if groups is None else groups

    def fetch(url: str) -> str:
        if log is not None:
            log.append(url)
        if url == competitions_api_url("22", "3"):
            return json.dumps(comps)
        for cid in [c["codigo"] for c in COMPS]:
            if url == groups_url(cid):
                payload = table.get(cid, [])
                if isinstance(payload, Exception):
                    raise payload
                return payload if isinstance(payload, str) else json.dumps(payload)
        raise AssertionError(url)

    return fetch


def test_report_has_import_catalog_format_and_official_metadata() -> None:
    log: list[str] = []
    rep = discover("22", "3", make_fetch(log=log))
    assert len(log) == 1 + 50 == rep["summary"]["requests"]
    assert rep["summary"]["competitions"] == 50 and rep["summary"]["competitions_active"] == 35
    assert rep["summary"]["incidents"] == 0
    by_comp = {c["competition_id"]: c for c in rep["competitions"]}
    assert sum(not c["active"] for c in by_comp.values()) == 15  # inactivas incluidas y marcadas
    assert by_comp["26750017"]["groups_status"] == "ok" and len(by_comp["26750017"]["group_ids"]) == 9
    assert by_comp["26738241"]["groups_status"] == "no_groups"  # el fixture solo trae grupos de 2
    g = next(g for g in rep["groups"] if g["competition_id"] == "26750017")
    assert set(g) >= {"competition_id", "group_id", "status", "league_path", "competition_meta", "expected_rounds",
                      "expected_teams", "delegation", "has_standings", "has_scorers", "competition_name", "group_name"}
    assert g["status"] == "pending" and g["league_path"] is None
    assert g["competition_meta"]["NombreCategoria"] and g["competition_meta"]["CodigoCategoria"]
    assert (g["expected_rounds"], g["delegation"], g["has_standings"], g["has_scorers"]) == (
        9, "CENTRAL R.F.F.M.", True, True)
    assert isinstance(g["expected_teams"], int)
    assert len(rep["groups"]) == 14  # 9 + 5


def test_inactive_competition_keeps_active_false_and_raw_meta() -> None:
    inactive = next(c for c in COMPS if c["Activa"] != "1")
    groups = {inactive["codigo"]: [{"codigo": "9", "nombre": "G", "total_jornadas": "3", "total_equipos": "4",
                                    "ver_clasificacion": "0", "clasificacion_goleadores": "0",
                                    "nombre_delegacion": "X"}]}
    rep = discover("22", "3", make_fetch(groups=groups))
    g = rep["groups"][0]
    assert g["active"] is False and g["competition_meta"] == inactive
    assert (g["has_standings"], g["has_scorers"]) == (False, False)


def test_invalid_entries_are_discarded_with_incidents() -> None:
    comps = deepcopy(COMPS[:3]) + [{"nombre": "SIN CODIGO", "codigo_tipo_juego": "3"},
                                   {"codigo": "", "nombre": "VACIO"},
                                   {"codigo": "77", "nombre": "OTRO TIPO", "codigo_tipo_juego": "1"},
                                   {"codigo": COMPS[0]["codigo"], "nombre": "DUP", "codigo_tipo_juego": "3"},
                                   {"codigo": "78", "nombre": "", "codigo_tipo_juego": "3"},
                                   "basura", None, {"codigo": ["x"], "nombre": "TIPO MALO"}]
    first = COMPS[0]["codigo"]
    groups = {first: [{"codigo": "5", "nombre": "A", "total_jornadas": "abc", "total_equipos": "4"},
                      {"nombre": "sin codigo"}, {"codigo": "5", "nombre": "dup"}, "x"]}
    rep = discover("22", "3", make_fetch(comps, groups))
    codes = sorted(i["code"] for i in rep["incidents"])
    assert codes == sorted(["competition_without_code"] * 5 + ["game_type_mismatch", "duplicate_competition",
                                                              "competition_without_name", "invalid_count",
                                                              "group_without_code", "group_without_code",
                                                              "duplicate_group"])
    assert [c["competition_id"] for c in rep["competitions"]] == [c["codigo"] for c in COMPS[:3]]
    assert [g["group_id"] for g in rep["groups"]] == ["5"] and rep["groups"][0]["expected_rounds"] is None


def test_failures_are_isolated_per_competition_and_catalog_failure_aborts() -> None:
    ids = [c["codigo"] for c in COMPS[:3]]
    groups: dict[str, Any] = {ids[0]: FetchError("HTTP 500"), ids[1]: "null", ids[2]: "{roto"}
    rep = discover("22", "3", make_fetch(COMPS[:3], groups))
    assert [c["groups_status"] for c in rep["competitions"]] == ["fetch_failed"] * 3
    assert [i["code"] for i in rep["incidents"]] == ["groups_unavailable"] * 3
    with pytest.raises(RuntimeError):
        discover("22", "3", lambda url: "null")
    with pytest.raises(RuntimeError):
        discover("22", "3", lambda url: (_ for _ in ()).throw(FetchError("HTTP 500")))


def test_plan_requests_is_1_plus_c_without_network() -> None:
    urls = plan_requests("22", 50)
    assert len(urls) == 51 and urls[0] == "https://www.rffm.es/api/competitions?temporada=22&tipojuego=3"
    assert urls[1].endswith("/api/groups?competicion=<C1>")
    real = plan_requests("22", 2, ["10", "11"])
    assert real[1:] == [groups_url("10"), groups_url("11")]


def test_diff_detects_adds_removals_and_changes() -> None:
    prev = discover("22", "3", make_fetch())
    new = deepcopy(prev)
    assert diff(prev, new).empty
    removed = new["competitions"].pop(0)
    new["groups"] = [g for g in new["groups"] if g["competition_id"] != removed["competition_id"]]
    new["competitions"].append({"competition_id": "NEW", "name": "Nueva", "active": True, "groups_status": "ok",
                                "group_ids": ["NG"], "competition_meta": {}})
    c = next(c for c in new["competitions"] if c["competition_id"] == "26750017")
    c["name"], c["active"] = "RENOMBRADA", not c["active"]
    c["group_ids"] = c["group_ids"][1:] + ["EXTRA"]
    new["groups"][0]["group_name"] = "Otro nombre"
    d = diff(prev, new)
    assert d.competitions_added == ["NEW"] and d.competitions_removed == [removed["competition_id"]]
    assert ("26750017", "EXTRA") in d.groups_added and ("NEW", "NG") in d.groups_added
    assert len([g for g in d.groups_removed if g[0] == "26750017"]) == 1
    fields = {(x["kind"], x["field"]) for x in d.changes}
    assert fields >= {("competition", "name"), ("competition", "active"), ("group", "name")}


def test_diff_ignores_group_changes_when_fetch_failed() -> None:
    prev = discover("22", "3", make_fetch())
    new = deepcopy(prev)
    c = next(c for c in new["competitions"] if c["competition_id"] == "26750017")
    c.update(groups_status="fetch_failed", group_ids=[])
    assert diff(prev, new).groups_removed == []


def build_dir(tmp_path: Path, drop_groups: set[str] = frozenset()) -> Path:  # type: ignore[assignment]
    """Directorio con el formato de data/raw/rffm/p0: NN.json + requests.jsonl (cuerpos de fixtures)."""
    d = tmp_path / "p0"
    d.mkdir()
    entries = [(competitions_api_url("22", "3"), json.dumps(COMPS))]
    entries += [(groups_url(c["codigo"]), json.dumps(GROUPS.get(c["codigo"], []))) for c in COMPS
                if c["codigo"] not in drop_groups]
    with (d / "requests.jsonl").open("w", encoding="utf-8") as log:
        for n, (url, body) in enumerate(entries, start=1):
            (d / f"{n:02d}.json").write_text(body, encoding="utf-8")
            log.write(json.dumps({"n": n, "url": url, "status": 200, "file": f"data/raw/rffm/p0/{n:02d}.json"}) + "\n")
    return d


def test_fetch_from_dir_matches_inline_fetch(tmp_path: Path) -> None:
    d = build_dir(tmp_path)
    fixed = lambda: datetime(2026, 11, 9, tzinfo=UTC)
    assert discover("22", "3", fetch_from_dir(d), now=fixed) == discover("22", "3", make_fetch(), now=fixed)
    with pytest.raises(FetchError):
        fetch_from_dir(d)("https://www.rffm.es/api/groups?competicion=0")


@pytest.fixture()
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> str:
        raise AssertionError("red prohibida")

    monkeypatch.setattr(client, "fetch_html", boom)
    monkeypatch.delenv("FUTSAL_RFFM_NETWORK_AUTHORIZED", raising=False)


def test_cli_plan_prints_urls_without_network(no_network: None, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["discover-catalog-v2", "--season", "22", "--plan"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 51 and lines[0].startswith("https://www.rffm.es/api/competitions?temporada=22")


def test_cli_from_dir_writes_report_where_import_catalog_reads_it(
        no_network: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d, out = build_dir(tmp_path), tmp_path / "out"
    assert main(["discover-catalog-v2", "--season", "22", "--from-dir", str(d), "--output", str(out)]) == 0
    target = out / "s22_t3" / REPORT_NAME
    rep = json.loads(target.read_text(encoding="utf-8"))
    assert len(rep["groups"]) == 14 and rep["groups"][0]["status"] == "pending"
    # reejecución: conserva el estado de descarga ya registrado y no marca cambios
    rep["groups"][0].update(status="downloaded", league_path="x.json")
    target.write_text(json.dumps(rep), encoding="utf-8")
    capsys.readouterr()
    assert main(["discover-catalog-v2", "--season", "22", "--from-dir", str(d), "--output", str(out)]) == 0
    assert "altas=0 bajas=0" in capsys.readouterr().out
    again = json.loads(target.read_text(encoding="utf-8"))
    assert (again["groups"][0]["status"], again["groups"][0]["league_path"]) == ("downloaded", "x.json")
    # --plan con --from-dir lista las URLs concretas
    assert main(["discover-catalog-v2", "--season", "22", "--from-dir", str(d), "--plan"]) == 0
    assert groups_url(COMPS[0]["codigo"]) in capsys.readouterr().out


def test_cli_refuses_network_without_dir_or_explicit_authorization(
        no_network: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert main(["discover-catalog-v2", "--season", "22", "--output", str(tmp_path)]) == 2
    assert "rechazado" in capsys.readouterr().out
    monkeypatch.setenv("FUTSAL_RFFM_NETWORK_AUTHORIZED", "1")
    assert main(["discover-catalog-v2", "--season", "22", "--output", str(tmp_path)]) == 2
    assert "no está implementada" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())
