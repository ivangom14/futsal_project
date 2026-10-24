"""Ayudas de las pruebas de clasificaciones y goleadores: sitio RFFM simulado con las fixtures sintéticas (sin red)."""
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from futsal.ingestion.rffm.client import FetchError

FIX = Path("tests/fixtures/rffm_tables")
COMP, GROUP, SEASON = "90000001", "90000002", "99"


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def inventory(groups: list[dict[str, object]] | None = None, competition: str = COMP) -> dict[str, object]:
    groups = groups if groups is not None else [{"external_id": GROUP, "name": "Grupo 1", "expected_rounds": 10, "expected_teams": 6}]
    return {"season": {"external_id": SEASON, "name": "2099-2100"}, "game_type": "3", "entries": [{
        "identification": "confirmed", "groups_status": "ok",
        "competition": {"external_id": competition, "name": "LIGA EJEMPLO FS"}, "groups": groups}]}


class FakeSite:
    """`fetch(url)` como `PoliteFetcher`: responde según `routes[(clase, grupo, jornada)]`; una lista se consume en orden."""

    def __init__(self, routes: dict[tuple[str, str, str | None], object]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        key = (parsed.path.rsplit("/", 1)[-1], q["grupo"][0], q.get("jornada", [None])[0])
        item = self.routes.get(key)
        if isinstance(item, list):
            item = item.pop(0) if len(item) > 1 else item[0]
        if item is None:
            raise FetchError("HTTP 404")
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, str)
        return item

    @property
    def requests(self) -> int:
        return len(self.calls)


Route = Callable[[str], str]
