"""Configuración de base de datos desde el entorno (y `.env` local si existe)."""

import os
from pathlib import Path


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip("'\"")
    return values


def database_url() -> str:
    """`DATABASE_URL` del entorno; si falta, la de `.env`."""
    url = os.environ.get("DATABASE_URL") or _load_dotenv(Path(".env")).get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL no definida (ver .env.example)")
    return url
