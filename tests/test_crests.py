"""Escudos: extracción sin red desde instantáneas RFFM y endpoint `/chat/crests` con CRESTS_MODE (docs/design-system/04-escudos-equipos.md)."""

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from futsal.agent.web import AgentRunner, create_chat_app
from futsal.importer.crest_backfill import crest_path, extract_crests, scan

FIXTURES = Path(__file__).parent / "fixtures"


def _next_data(name: str) -> Any:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return json.loads(re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL).group(1))  # type: ignore[union-attr]


def test_crest_path_solo_acepta_imagenes_de_la_rffm() -> None:
    assert crest_path("/pnfg/pimg/Clubes/00100_0000002531_download.jpg?nova=1") == "/pnfg/pimg/Clubes/00100_0000002531_download.jpg"
    assert crest_path("https://appweb.rffm.es/pnfg/pimg/Clubes/a.png") == "/pnfg/pimg/Clubes/a.png"
    for bad in ("https://otro.com/pnfg/pimg/x.png", "javascript:alert(1)", "/pnfg/pimg/x.svg", "/otra/ruta.png", "", None, 42):
        assert crest_path(bad) is None


def test_jornada_real_trae_un_escudo_por_equipo() -> None:
    crests = extract_crests(_next_data("rffm_round_2.html"))
    assert len(crests) == 14  # 7 partidos, 14 equipos
    assert all(p.startswith("/pnfg/pimg/Clubes/") for p in crests.values())


def test_acta_real_y_clasificacion() -> None:
    acta = extract_crests(_next_data("rffm_match_report.html"))
    assert len(acta) == 2 and all("?" not in p for p in acta.values())
    tabla = extract_crests({"clasificacion": [{"codequipo": "123", "nombre": "X", "url_img": "/pnfg/pimg/Clubes/x.png"},
                                              {"codequipo": "124", "url_img": "https://malo.example/x.png"}]})
    assert tabla == {"123": "/pnfg/pimg/Clubes/x.png"}


def test_scan_recorre_html_y_json_y_gana_la_instantanea_mas_reciente(tmp_path: Path) -> None:
    old, new = tmp_path / "a" / "old.json", tmp_path / "b" / "new.html"
    old.parent.mkdir()
    new.parent.mkdir()
    old.write_text(json.dumps([{"CodEquipo_local": "7", "url_img_local": "/pnfg/pimg/Clubes/viejo.png"}]), encoding="utf-8")
    payload = json.dumps({"props": {"x": [{"codigo_equipo_local": "7", "escudo_equipo_local": "/pnfg/pimg/Clubes/nuevo.png"}]}})
    new.write_text(f'<html><script id="__NEXT_DATA__" type="application/json">{payload}</script></html>', encoding="utf-8")
    (tmp_path / "roto.json").write_text("{no es json", encoding="utf-8")
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))
    crests, read = scan(tmp_path)
    assert crests == {"7": "/pnfg/pimg/Clubes/nuevo.png"}
    assert read == 2  # el JSON roto se ignora


def _chat(source: Any) -> TestClient:
    return TestClient(create_chat_app(AgentRunner(), lambda: {"reachable": True, "detail": None}, crests_source=source))


def test_chat_crests_apagado_por_defecto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRESTS_MODE", raising=False)
    monkeypatch.chdir(FIXTURES)  # sin .env del proyecto
    called: list[int] = []

    def source() -> dict[str, str]:
        called.append(1)
        return {}

    res = _chat(source).get("/chat/crests")
    assert res.json() == {"mode": "off", "crests": {}}
    assert called == []  # ni siquiera consulta la API


def test_chat_crests_link_usa_cache_y_sobrevive_a_errores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRESTS_MODE", "link")
    calls: list[int] = []

    def source() -> dict[str, str]:
        calls.append(1)
        if len(calls) > 1:
            raise httpx.ConnectError("caída")
        return {"C.D. DOSA": "https://appweb.rffm.es/pnfg/pimg/Clubes/dosa.png"}

    client = _chat(source)
    first = client.get("/chat/crests").json()
    assert first == {"mode": "link", "crests": {"C.D. DOSA": "https://appweb.rffm.es/pnfg/pimg/Clubes/dosa.png"}}
    assert client.get("/chat/crests").json() == first and calls == [1]  # en caché

    failing = _chat(lambda: (_ for _ in ()).throw(httpx.ConnectError("caída")))
    body = failing.get("/chat/crests").json()
    assert body["crests"] == {} and body["error"] == "ConnectError"
