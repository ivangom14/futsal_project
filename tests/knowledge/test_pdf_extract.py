"""Extracción de PDF: PDF mínimo generado en el test y, si existe, el PDF real de FIFA."""
import json
from pathlib import Path

import httpx
import pytest

from futsal.knowledge import fetch as fetch_mod
from futsal.knowledge.extract import ExtractionError, PdfExtractor, get_extractor
from futsal.knowledge.fetch import FetchError, fetch_document
from futsal.knowledge.profiles import FIFA_FUTSAL_LAWS
from futsal.knowledge.quality import assess
from futsal.knowledge.structure import build_structure


def make_pdf(pages: list[str]) -> bytes:
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(f'{3 + 2 * i} 0 R' for i in range(len(pages)))}] "
            f"/Count {len(pages)} >>"]
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {4 + 2 * i} 0 R "
                    f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    out = b"%PDF-1.4\n"
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def test_extracts_pages_in_order_with_numbers(tmp_path: Path) -> None:
    f = tmp_path / "a.pdf"
    f.write_bytes(make_pdf(["Primera pagina", "Segunda pagina"]))
    doc = get_extractor(f).extract(f)
    assert [(p.number, p.text.strip()) for p in doc.pages] == [(1, "Primera pagina"), (2, "Segunda pagina")]


def test_invalid_or_unsupported_files_fail_cleanly(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"no soy un pdf")
    with pytest.raises(ExtractionError):
        PdfExtractor().extract(bad)
    with pytest.raises(ExtractionError):
        get_extractor(tmp_path / "x.docx")


class _FakeStream:
    def __init__(self, data: bytes) -> None:
        self.data, self.headers = data, {"content-type": "application/pdf"}

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self) -> list[bytes]:
        return [self.data]


def test_fetch_records_provenance_reuses_file_and_rejects_non_pdf(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake(method: str, url: str, **kw: object) -> _FakeStream:
        calls.append(url)
        return _FakeStream(make_pdf(["x"]) if "ok" in url else b"<html>")

    monkeypatch.setattr(fetch_mod.httpx, "stream", fake)
    info = fetch_document("https://x/ok.pdf", tmp_path / "d")
    assert len(str(info["sha256"])) == 64
    assert json.loads((tmp_path / "d" / "source.json").read_text())["url"] == "https://x/ok.pdf"
    assert fetch_document("https://x/ok.pdf", tmp_path / "d")["reused"] == 1 and len(calls) == 1
    with pytest.raises(FetchError):
        fetch_document("https://x/html", tmp_path / "e")
    assert not (tmp_path / "e" / "original.pdf").exists()

    def boom(*a: object, **k: object) -> _FakeStream:
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr(fetch_mod.httpx, "stream", boom)
    with pytest.raises(FetchError):
        fetch_document("https://x/ok.pdf", tmp_path / "f")


REAL = Path("data/knowledge/fifa-futsal-laws-2025-26/original.pdf")


@pytest.mark.skipif(not REAL.exists(), reason="PDF de FIFA no descargado (knowledge-fetch)")
def test_real_fifa_pdf_structure_and_page_references() -> None:
    doc = PdfExtractor().extract(REAL)
    s = build_structure(doc, FIFA_FUTSAL_LAWS)
    q = assess(doc, s, FIFA_FUTSAL_LAWS)
    assert q.total_pages == 197 and q.garbled_chars == 0 and q.ok
    assert q.rules_detected == list(range(1, 18)) and not q.rules_start_mismatch
    assert q.flagged["sin_texto"] == [197]

    def find(rule: int, section: str | None, needle: str) -> tuple[int, int]:
        for g in s.segments:
            if g.rule_number == rule and g.section_number == section and needle in g.text:
                return g.page_start, g.page_end
        raise AssertionError(f"no encontrado: regla {rule} §{section} {needle!r}")

    assert find(3, "1", "máximo de cinco jugadores") == (31, 31)
    assert find(11, None, "No existe la infracción de fuera de juego") == (75, 75)
    assert any(g.rule_number == 12 and g.subsection_title == "Juego peligroso" and g.page_start == 80
               for g in s.segments)
