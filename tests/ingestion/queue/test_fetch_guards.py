"""Guardas del Fetcher que no necesitan base de datos, y independencia de la cola respecto del agente."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from futsal.ingestion.queue.breaker import CircuitBreaker
from futsal.ingestion.queue.fetch import Fetcher, NetworkDisabled
from futsal.ingestion.queue.ratelimit import RateLimiter


def fetcher(handler: Any, *, authorized: bool = False) -> Fetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Fetcher(cast(Any, None), "rffm", RateLimiter(), CircuitBreaker("rffm"), client=client, network_authorized=authorized)


def test_disallowed_host_rejected_before_anything_else() -> None:
    def boom(_r: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe haber petición")

    with pytest.raises(ValueError, match="Host no permitido"):
        fetcher(boom, authorized=True).get("https://example.com/x")


def test_network_disabled_by_default() -> None:
    def boom(_r: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe haber petición")

    f = fetcher(boom)
    assert f.network_authorized is False
    with pytest.raises(NetworkDisabled):
        f.get("https://www.rffm.es/api/groups?competicion=1")
    assert f.requests == 0


def test_queue_does_not_import_agent() -> None:
    code = ("import sys, futsal.ingestion.queue as q\n"
            "import futsal.ingestion.queue.jobs, futsal.ingestion.queue.ratelimit, futsal.ingestion.queue.breaker\n"
            "import futsal.ingestion.queue.controls, futsal.ingestion.queue.fetch, futsal.ingest_commands\n"
            "bad = sorted(m for m in sys.modules if m == 'futsal.agent' or m.startswith('futsal.agent.'))\n"
            "print(bad)\nsys.exit(1 if bad else 0)\n")
    src = str(Path(__file__).parents[3] / "src")
    env = {**os.environ, "PYTHONPATH": src}
    done = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stdout + done.stderr
