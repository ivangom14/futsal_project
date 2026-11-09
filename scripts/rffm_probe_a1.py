"""Sondeo A1 de la RFFM (solo lectura, tope absoluto de 20 peticiones GET).

Uso: python scripts/rffm_probe_a1.py "<url>" "<motivo>" [--max N --out DIR]
(por defecto, A1: 20 peticiones en data/raw/rffm/probe-a1; P0 usa --max 51 --out data/raw/rffm/p0)
Cada llamada hace como mucho UNA petición, espera >= 5 s desde la anterior, guarda el cuerpo en
data/raw/rffm/probe-a1/NN.<ext> y añade una línea a data/raw/rffm/probe-a1/requests.jsonl.
Se niega a ejecutar si el registro ya tiene 20 peticiones o si el host no es de la RFFM.
"""

import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from futsal.ingestion.rffm.client import ALLOWED_HOSTS, USER_AGENT

MAX_REQUESTS = 20
MIN_INTERVAL_S = 5.0
OUT = Path("data/raw/rffm/probe-a1")
LOG = OUT / "requests.jsonl"


def _configure(argv: list[str]) -> list[str]:
    global MAX_REQUESTS, OUT, LOG
    rest = []
    it = iter(argv)
    for a in it:
        if a == "--max":
            MAX_REQUESTS = int(next(it))
        elif a == "--out":
            OUT = Path(next(it))
            LOG = OUT / "requests.jsonl"
        else:
            rest.append(a)
    return rest


def _entries() -> list[dict[str, object]]:
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line]


def main(url: str, reason: str) -> int:
    if urlparse(url).hostname not in ALLOWED_HOSTS:
        print("host no permitido", file=sys.stderr)
        return 2
    entries = _entries()
    if len(entries) >= MAX_REQUESTS:
        print(f"tope de {MAX_REQUESTS} peticiones alcanzado", file=sys.stderr)
        return 3
    if entries:
        last = datetime.fromisoformat(str(entries[-1]["at"])).timestamp()
        wait = MIN_INTERVAL_S - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
    OUT.mkdir(parents=True, exist_ok=True)
    n = len(entries) + 1
    started = time.monotonic()
    entry: dict[str, object] = {"n": n, "at": datetime.now(UTC).isoformat(), "method": "GET",
                                "url": url, "reason": reason}
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=30.0,
                         follow_redirects=True)
        body = resp.content
        ext = "json" if "json" in resp.headers.get("content-type", "") else "html"
        path = OUT / f"{n:02d}.{ext}"
        path.write_bytes(body)
        entry.update(status=resp.status_code, bytes=len(body), file=str(path),
                     sha256=hashlib.sha256(body).hexdigest(), final_url=str(resp.url),
                     content_type=resp.headers.get("content-type"))
    except httpx.HTTPError as exc:
        entry.update(status=None, error=f"{type(exc).__name__}: {exc}")
    entry["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(json.dumps({k: entry.get(k) for k in ("n", "status", "bytes", "file", "error",
                                                 "elapsed_ms")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    args = _configure(sys.argv[1:])
    sys.exit(main(args[0], args[1]))
