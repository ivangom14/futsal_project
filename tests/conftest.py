"""Utilidades de prueba compartidas."""
import logging
import os
from collections.abc import Callable

import pytest

_LLM_ENV = ("LLM_PROVIDER", "LLM_MODEL", "GEMINI_MODEL", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_MODEL")


@pytest.fixture(autouse=True)
def _isolate_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las pruebas no dependen del entorno del desarrollador: las que necesitan estas variables las fijan ellas."""
    for name in [*_LLM_ENV, *(k for k in os.environ if k.startswith("LLM_MODEL_"))]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _enable_futsal_loggers() -> None:
    """`alembic/env.py` llama a `fileConfig` (deshabilita los loggers ya creados) al migrar en pruebas de integración: sin esto, `caplog` queda vacío en las pruebas posteriores."""
    for name, lg in logging.root.manager.loggerDict.items():
        if name.startswith("futsal") and isinstance(lg, logging.Logger):
            lg.disabled = False


PdfMaker = Callable[[list[str]], bytes]


def _make_pdf(pages: list[str]) -> bytes:
    """PDF mínimo (una línea de texto Helvetica por página) para pruebas sin ficheros binarios."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(f'{3 + 2 * i} 0 R' for i in range(len(pages)))}] "
            f"/Count {len(pages)} >>"]
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                    f"/Contents {4 + 2 * i} 0 R /Resources << /Font << /F1 << /Type /Font "
                    f"/Subtype /Type1 /BaseFont /Helvetica >> >> >> >>")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    out = b"%PDF-1.4\n"
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
            .encode())
    return out


@pytest.fixture()
def make_pdf() -> PdfMaker:
    return _make_pdf
