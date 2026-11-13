"""Escudos: extracción sin red desde instantáneas RFFM y endpoint `/chat/crests` con CRESTS_MODE (docs/design-system/04-escudos-equipos.md)."""

import io
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from futsal.agent.web import AgentRunner, create_chat_app
from futsal.importer.crest_backfill import crest_path, extract_crests, scan
from futsal.importer.crest_store import MAX_BYTES, download_crests, http_fetch, normalize

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


def _chat(source: Any, folder: Path | None = None) -> TestClient:
    return TestClient(create_chat_app(AgentRunner(), lambda: {"reachable": True, "detail": None}, crests_source=source, crests_folder=folder))


ROWS: list[dict[str, Any]] = [{"team_id": 1, "name": "C.D. DOSA", "crest_url": "https://appweb.rffm.es/pnfg/pimg/Clubes/dosa.png", "file": "0123456789abcdef0123.webp"},
        {"team_id": 2, "name": "FS ALCALA", "crest_url": "https://appweb.rffm.es/pnfg/pimg/Clubes/alcala.png", "file": None}]


def test_chat_crests_apagado_por_defecto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRESTS_MODE", raising=False)
    monkeypatch.chdir(FIXTURES)  # sin .env del proyecto
    called: list[int] = []

    def source() -> list[dict[str, Any]]:
        called.append(1)
        return ROWS

    client = _chat(source)
    assert client.get("/chat/crests").json() == {"mode": "off", "crests": {}}
    assert client.get("/chat/crests/img/0123456789abcdef0123.webp").status_code == 404
    assert called == []  # ni siquiera consulta la API


def test_chat_crests_link_usa_cache_y_sobrevive_a_errores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRESTS_MODE", "link")
    calls: list[int] = []

    def source() -> list[dict[str, Any]]:
        calls.append(1)
        if len(calls) > 1:
            raise httpx.ConnectError("caída")
        return ROWS

    client = _chat(source)
    first = client.get("/chat/crests").json()
    assert first == {"mode": "link", "crests": {"C.D. DOSA": ROWS[0]["crest_url"], "FS ALCALA": ROWS[1]["crest_url"]}}
    assert client.get("/chat/crests").json() == first and calls == [1]  # en caché

    def failing() -> list[dict[str, Any]]:
        raise httpx.ConnectError("caída")

    body = _chat(failing).get("/chat/crests").json()
    assert body["crests"] == {} and body["error"] == "ConnectError"


def test_chat_crests_local_sirve_las_copias(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CRESTS_MODE", "local")
    webp = normalize(_png_rgba())
    (tmp_path / ROWS[0]["file"]).write_bytes(webp)
    client = _chat(lambda: ROWS, tmp_path)
    assert client.get("/chat/crests").json() == {"mode": "local", "crests": {"C.D. DOSA": f"/chat/crests/img/{ROWS[0]['file']}"}}  # sin copia → monograma
    img = client.get(f"/chat/crests/img/{ROWS[0]['file']}")
    assert img.status_code == 200 and img.content == webp
    assert img.headers["content-type"] == "image/webp" and "immutable" in img.headers["cache-control"]
    for bad in ("ffffffffffffffffffff.webp", "..%2Findex.json", "index.json", "0123456789abcdef0123.png"):
        assert client.get(f"/chat/crests/img/{bad}").status_code == 404


# --- copias locales: normalización y descarga -------------------------------------------------------------------------

def _jpeg(w: int, h: int, color: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, "JPEG")
    return out.getvalue()


def _png_rgba() -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (100, 112), (200, 30, 30, 0)).save(out, "PNG")
    return out.getvalue()


def test_normalize_da_webp_96_transparente_sin_deformar() -> None:
    for raw in (_jpeg(100, 66), _jpeg(1268, 1796), _png_rgba()):
        with Image.open(io.BytesIO(normalize(raw))) as img:
            assert (img.format, img.size, img.mode) == ("WEBP", (96, 96), "RGBA")
            corner = img.getpixel((0, 0))
            assert isinstance(corner, tuple) and corner[3] == 0  # esquina transparente: la imagen va centrada con «contain»
    big = normalize(_jpeg(1268, 1796))
    assert len(big) < len(_jpeg(1268, 1796))


def test_normalize_rechaza_lo_que_no_es_imagen() -> None:
    for raw in (b"<html>403</html>", b"", b"\x89PNG\r\n\x1a\nroto", b"x" * (MAX_BYTES + 1)):
        with pytest.raises(ValueError):
            normalize(raw)


def test_http_fetch_solo_appweb() -> None:
    with pytest.raises(ValueError):
        http_fetch("https://evil.example/pnfg/pimg/Clubes/x.png")


def test_download_crests_idempotente_con_pausas_y_errores(tmp_path: Path) -> None:
    served = {"/pnfg/pimg/Clubes/a.jpg": _jpeg(100, 100, (10, 20, 30)), "/pnfg/pimg/Clubes/b.png": _png_rgba(), "/pnfg/pimg/Clubes/roto.png": b"<html>"}
    fetched: list[str] = []
    sleeps: list[float] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return served[url.removeprefix("https://appweb.rffm.es")]

    stats = download_crests(list(served), tmp_path, fetch=fetch, delay=0.5, sleep=sleeps.append)
    assert (stats["downloaded"], stats["failed"], stats["already"]) == (2, 1, 0)
    assert sleeps == [0.5, 0.5]  # una pausa entre peticiones, nunca antes de la primera
    assert all(u.startswith("https://appweb.rffm.es/pnfg/") for u in fetched)
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert set(index) == {"/pnfg/pimg/Clubes/a.jpg", "/pnfg/pimg/Clubes/b.png"}
    for entry in index.values():
        assert (tmp_path / entry["file"]).is_file() and entry["bytes"] > 0

    fetched.clear()
    again = download_crests(list(served), tmp_path, fetch=fetch, delay=0.5, sleep=sleeps.append)
    assert (again["downloaded"], again["already"]) == (0, 2) and fetched == ["https://appweb.rffm.es/pnfg/pimg/Clubes/roto.png"]  # solo reintenta el fallido

    (tmp_path / index["/pnfg/pimg/Clubes/a.jpg"]["file"]).unlink()  # archivo borrado a mano → se vuelve a bajar
    fetched.clear()
    download_crests(["/pnfg/pimg/Clubes/a.jpg"], tmp_path, fetch=fetch, sleep=sleeps.append)
    assert fetched == ["https://appweb.rffm.es/pnfg/pimg/Clubes/a.jpg"]
