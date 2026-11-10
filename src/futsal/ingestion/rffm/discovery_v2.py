"""Descubrimiento v2 del catálogo RFFM: `/api/competitions` + `/api/groups` (JSON). Puro: la descarga es inyectable.

Produce el informe que consume `import_catalog` (`{"groups": [...]}`), con los grupos aún sin descargar
(`status="pending"`, sin `league_path`), añadiendo los metadatos oficiales:
- por grupo: `competition_meta` (claves crudas de `/api/competitions`), `expected_rounds`, `expected_teams`,
  `delegation`, `has_standings` (`ver_clasificacion`), `has_scorers` (`clasificacion_goleadores`);
- `competitions`: todas las del catálogo (también las inactivas, con `active=false`) y sus grupos, para `diff`.
Sin lista manual de competiciones: lo que publica la RFFM es el alcance.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from futsal.ingestion.rffm.catalog_ingest import PENDING
from futsal.ingestion.rffm.client import (
    API_GROUPS_URL,
    FetchError,
    competitions_api_url,
    groups_url,
)
from futsal.ingestion.rffm.parser import RffmParseError

Fetch = Callable[[str], str]
CatalogReport = dict[str, Any]


def _s(value: Any) -> str | None:
    text = "" if value is None or isinstance(value, (dict, list)) else str(value).strip()
    return text or None


def _flag(value: Any) -> bool | None:
    return {"1": True, "0": False, "true": True, "false": False}.get(str(value).strip().lower()) if value is not None else None


def _count(value: Any) -> int | None:
    text = _s(value)
    return int(text) if text and text.isdigit() else None


def _load(text: Any) -> Any:
    if isinstance(text, (bytes, str)):
        return json.loads(text)
    return text


def plan_requests(season: str, n_competitions: int, competition_ids: Sequence[str] | None = None,
                  game_type: str = "3") -> list[str]:
    """URLs que haría `discover` (1 + C), sin red. Sin IDs conocidos, las de grupos llevan un marcador `<C#>`."""
    urls = [competitions_api_url(season, game_type)]
    for i in range(n_competitions):
        urls.append(groups_url(competition_ids[i]) if competition_ids else f"{API_GROUPS_URL}?competicion=<C{i + 1}>")
    return urls


def _incident(code: str, detail: str, competition_id: str | None = None, group_id: str | None = None) -> dict[str, Any]:
    return {"code": code, "detail": detail[:300], "competition_id": competition_id, "group_id": group_id}


def _groups(payload: Any, comp_id: str, incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise RffmParseError("la respuesta de grupos no es una lista" + (" (null)" if payload is None else ""))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for g in payload:
        gid = _s(g.get("codigo")) if isinstance(g, dict) else None
        if not isinstance(g, dict) or not gid:
            incidents.append(_incident("group_without_code", "grupo sin código descartado", comp_id))
            continue
        if gid in seen:
            incidents.append(_incident("duplicate_group", "grupo repetido descartado", comp_id, gid))
            continue
        seen.add(gid)
        rounds, teams = _count(g.get("total_jornadas")), _count(g.get("total_equipos"))
        for key, val in (("total_jornadas", rounds), ("total_equipos", teams)):
            if val is None and _s(g.get(key)) is not None:
                incidents.append(_incident("invalid_count", f"{key}={g.get(key)!r}", comp_id, gid))
        out.append({"group_id": gid, "group_name": _s(g.get("nombre")), "expected_rounds": rounds,
                    "expected_teams": teams, "delegation": _s(g.get("nombre_delegacion")),
                    "has_standings": _flag(g.get("ver_clasificacion")),
                    "has_scorers": _flag(g.get("clasificacion_goleadores"))})
    return out


def discover(season: str, game_type: str, fetch: Fetch, *,
             now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> CatalogReport:
    """Informe del catálogo: 1 petición de competiciones + 1 de grupos por competición (`fetch(url)` devuelve JSON)."""
    url = competitions_api_url(season, game_type)
    try:
        raw = _load(fetch(url))
    except (FetchError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"No se pudo obtener el catálogo de competiciones: {exc}") from exc
    if not isinstance(raw, list):
        raise RuntimeError("El catálogo de competiciones no es una lista (¿null u otro ámbito?)")  # noqa: TRY004
    incidents: list[dict[str, Any]] = []
    competitions: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        cid = _s(item.get("codigo")) if isinstance(item, dict) else None
        name = _s(item.get("nombre")) if isinstance(item, dict) else None
        if not isinstance(item, dict) or not cid:
            incidents.append(_incident("competition_without_code", "entrada sin código descartada"))
            continue
        if cid in seen:
            incidents.append(_incident("duplicate_competition", "competición repetida descartada", cid))
            continue
        if not name:
            incidents.append(_incident("competition_without_name", "entrada sin nombre descartada", cid))
            continue
        if _s(item.get("codigo_tipo_juego")) != game_type:
            incidents.append(_incident("game_type_mismatch",
                                       f"codigo_tipo_juego={item.get('codigo_tipo_juego')!r} != {game_type!r}", cid))
            continue
        seen.add(cid)
        active = _flag(item.get("Activa"))
        if active is None:
            incidents.append(_incident("invalid_active", f"Activa={item.get('Activa')!r}", cid))
        comp: dict[str, Any] = {"competition_id": cid, "name": name, "active": bool(active), "groups_status": "ok",
                                "competition_meta": dict(item), "group_ids": []}
        competitions.append(comp)
        try:
            found = _groups(_load(fetch(groups_url(cid))), cid, incidents)
        except (FetchError, RffmParseError, json.JSONDecodeError) as exc:
            comp["groups_status"] = "fetch_failed"
            incidents.append(_incident("groups_unavailable", str(exc), cid))
            continue
        if not found:
            comp["groups_status"] = "no_groups"
        for g in found:
            comp["group_ids"].append(g["group_id"])
            groups.append({"competition_id": cid, "competition_name": name, "active": bool(active),
                           "status": PENDING, "league_path": None, "competition_meta": dict(item), **g})
    return {
        "source": "api_v2", "season": {"external_id": season}, "game_type": game_type, "source_url": url,
        "generated_at": now().isoformat(), "competitions": competitions, "groups": groups, "incidents": incidents,
        "summary": {"competitions": len(competitions), "competitions_active": sum(c["active"] for c in competitions),
                    "groups": len(groups), "incidents": len(incidents),
                    "requests": 1 + len(competitions),
                    "competitions_without_groups": sum(c["groups_status"] == "no_groups" for c in competitions),
                    "group_requests_failed": sum(c["groups_status"] == "fetch_failed" for c in competitions)},
    }


@dataclass
class CatalogDiff:
    competitions_added: list[str] = field(default_factory=list)
    competitions_removed: list[str] = field(default_factory=list)  # candidatas a `removed_at`
    groups_added: list[tuple[str, str]] = field(default_factory=list)  # (competición, grupo)
    groups_removed: list[tuple[str, str]] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)  # {kind, id, field, old, new}

    @property
    def empty(self) -> bool:
        return not (self.competitions_added or self.competitions_removed or self.groups_added
                    or self.groups_removed or self.changes)


def diff(previous_report: Mapping[str, Any], new_report: Mapping[str, Any]) -> CatalogDiff:
    """Altas, bajas y cambios de nombre / `Activa` / grupos entre dos informes. Una competición con
    `groups_status=fetch_failed` en alguno de los dos no genera altas ni bajas de grupos (no se sabe)."""
    out = CatalogDiff()
    old = {c["competition_id"]: c for c in previous_report.get("competitions", [])}
    new = {c["competition_id"]: c for c in new_report.get("competitions", [])}
    out.competitions_added = sorted(set(new) - set(old))
    out.competitions_removed = sorted(set(old) - set(new))
    for cid in sorted(set(old) & set(new)):
        o, n = old[cid], new[cid]
        for key in ("name", "active"):
            if o.get(key) != n.get(key):
                out.changes.append({"kind": "competition", "id": cid, "field": key, "old": o.get(key), "new": n.get(key)})
        if "fetch_failed" in (o.get("groups_status"), n.get("groups_status")):
            continue
        og, ng = set(o.get("group_ids", [])), set(n.get("group_ids", []))
        out.groups_added += [(cid, g) for g in sorted(ng - og)]
        out.groups_removed += [(cid, g) for g in sorted(og - ng)]
    for cid in out.competitions_added:
        out.groups_added += [(cid, g) for g in new[cid].get("group_ids", [])]
    for cid in out.competitions_removed:
        out.groups_removed += [(cid, g) for g in old[cid].get("group_ids", [])]
    old_names = {(g["competition_id"], g["group_id"]): g.get("group_name") for g in previous_report.get("groups", [])}
    for grp in new_report.get("groups", []):
        gkey = (grp["competition_id"], grp["group_id"])
        if gkey in old_names and old_names[gkey] != grp.get("group_name"):
            out.changes.append({"kind": "group", "id": grp["group_id"], "field": "name",
                                "old": old_names[gkey], "new": grp.get("group_name")})
    return out


def _normalize(url: str) -> str:
    parts = urlparse(url)
    return f"{parts.netloc}{parts.path}?{'&'.join(f'{k}={v}' for k, v in sorted(parse_qsl(parts.query)))}"


def fetch_from_dir(directory: Any) -> Fetch:
    """`fetch` que sirve cuerpos guardados (`NN.json` + `requests.jsonl`, formato de data/raw/rffm/p0); sin red."""
    root = Path(directory)
    index: dict[str, Path] = {}
    log = root / "requests.jsonl"
    if not log.exists():
        raise RuntimeError(f"no existe {log}")
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("status") == 200 and entry.get("file"):
            index.setdefault(_normalize(str(entry["url"])), root / Path(str(entry["file"])).name)

    def fetch(url: str) -> str:
        path = index.get(_normalize(url))
        if path is None or not path.exists():
            raise FetchError(f"sin cuerpo guardado para {url}")
        return path.read_text(encoding="utf-8")

    return fetch
