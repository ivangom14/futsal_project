"""Descarga de documentos públicos con procedencia (URL, hash, fecha de descarga)."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx


class FetchError(Exception):
    pass


def fetch_document(url: str, dest_dir: Path, *, name: str = "original.pdf", refresh: bool = False,
                   timeout: float = 120.0) -> dict[str, str | int]:
    """Descarga `url` en `dest_dir/name` y escribe `source.json`. Reutiliza el archivo si existe."""
    target = dest_dir / name
    source = dest_dir / "source.json"
    if target.exists() and source.exists() and not refresh:
        return dict(json.loads(source.read_text(encoding="utf-8")), reused=1)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            data = b"".join(resp.iter_bytes())
    except httpx.HTTPError as exc:
        raise FetchError(f"descarga fallida: {type(exc).__name__}") from exc
    if name.endswith(".pdf") and not data.startswith(b"%PDF"):
        raise FetchError("el contenido descargado no es un PDF")
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(target)
    info: dict[str, str | int] = {
        "url": url, "file": name, "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data), "content_type": content_type,
        "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds")}
    source.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info
