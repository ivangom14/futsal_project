"""Ingesta de todos los grupos confirmados de un inventario de catálogo (reutiliza `scrape_league`)."""

import json
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from futsal.ingestion.rffm.catalog import CONFIRMED, GROUPS_OK
from futsal.ingestion.rffm.client import FetchError, group_url
from futsal.ingestion.rffm.league import Target, check_identity, scrape_league
from futsal.ingestion.rffm.parser import RffmParseError, extract_page_props

Fetch = Callable[[str], str]
DOWNLOADED, PARTIAL, NO_ROUNDS, FAILED, PENDING = "downloaded", "partial", "no_rounds", "failed", "pending"
REPORT_NAME = "catalog-scrape-report.json"


class NoRounds(RuntimeError):
    """El grupo existe pero la web no publica ninguna jornada."""


def group_dir(out_root: Path, competition: str, group: str) -> Path:
    return out_root / f"c{competition}" / f"g{group}"


def iter_groups(inventory: dict[str, Any], only: set[str] | None = None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(competición, grupo) de las entradas confirmadas con grupos descubiertos; `only` filtra por ID de competición."""
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for entry in inventory["entries"]:
        comp = entry["competition"]
        if entry["identification"] != CONFIRMED or entry["groups_status"] != GROUPS_OK or comp is None:
            continue
        if only and comp["external_id"] not in only:
            continue
        pairs.extend((comp, g) for g in entry["groups"])
    return pairs


def _seed(target: Target, gdir: Path, fetch: Fetch, refresh: bool) -> str:
    """Jornada actual del grupo (sin suponer IDs): la web la devuelve si no se indica `jornada`."""
    path = gdir / "seed.html"
    if path.exists() and not refresh:
        html = path.read_text(encoding="utf-8")
    else:
        html = fetch(group_url(target.season, target.competition, target.group, target.game_type))
    props = extract_page_props(html)
    results = props.get("results") or {}
    rid = str(results.get("jornada") or "").strip()
    jornadas = (props.get("rounds") or {}).get("jornadas")
    if not rid or not jornadas:
        raise NoRounds("la web no publica jornadas para el grupo")
    bad = check_identity(html, target, rid)
    if bad:
        raise RffmParseError("identidad distinta: " + "; ".join(bad))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    snapshot = gdir / "rounds" / f"{rid}.html"
    if not snapshot.exists():
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(html, encoding="utf-8")
    return rid


def estimate_requests(inventory: dict[str, Any], out_root: Path, only: set[str] | None = None) -> dict[str, Any]:
    """Peticiones HTTP previstas (aproximación): jornadas esperadas menos snapshots locales."""
    total, unknown = 0, 0
    for comp, g in iter_groups(inventory, only):
        gdir = group_dir(out_root, comp["external_id"], g["external_id"])
        cached = len(list((gdir / "rounds").glob("*.html"))) if (gdir / "rounds").exists() else 0
        if g["expected_rounds"] is None:
            unknown += 1
            continue
        total += max(g["expected_rounds"] - cached, 0) + (0 if (gdir / "seed.html").exists() else 1)
    return {"groups": len(iter_groups(inventory, only)), "estimated_requests": total,
            "groups_without_expected_rounds": unknown}


def _scrape_group(target: Target, gdir: Path, fetch: Fetch, expected_rounds: int | None, *, refresh: bool,
                  resume: bool, max_rounds: int | None, requests_made: Callable[[], int]) -> dict[str, Any]:
    before = requests_made()
    base: dict[str, Any] = {"expected_rounds": expected_rounds, "league_path": None}
    try:
        seed = _seed(target, gdir, fetch, refresh)
        summary = scrape_league(target, gdir, fetch, seed, refresh=refresh, resume=resume,
                                max_rounds=max_rounds, requests_made=requests_made)
    except NoRounds as exc:
        return {**base, "status": NO_ROUNDS, "error": str(exc), "http_requests": requests_made() - before}
    except (FetchError, RffmParseError, RuntimeError, ValueError) as exc:
        return {**base, "status": FAILED, "error": str(exc)[:300], "http_requests": requests_made() - before}
    league = json.loads((gdir / "league.json").read_text(encoding="utf-8"))
    teams = {t for m in league["matches"] for t in (m["home_team_external_id"], m["away_team_external_id"]) if t}
    complete = (summary["rounds_failed"] == 0 and summary["rounds_selected"] == summary["rounds_discovered"])
    codes = Counter(i["code"] for i in summary["quality_issues"])
    return {
        **base, "status": DOWNLOADED if complete else PARTIAL, "error": None,
        "league_path": str(gdir / "league.json"),
        "rounds_discovered": summary["rounds_discovered"], "rounds_downloaded": summary["rounds_downloaded"],
        "rounds_from_cache": summary["rounds_from_cache"], "rounds_failed": summary["rounds_failed"],
        "failed_rounds": summary["failed_rounds"], "matches_total": summary["matches_total"],
        "matches_finished": summary["matches_finished"], "matches_scheduled": summary["matches_scheduled"],
        "teams_in_matches": len(teams), "expected_teams": None,
        "rounds_match_expected": None if expected_rounds is None else expected_rounds == summary["rounds_discovered"],
        "quality_issues": dict(codes), "http_requests": requests_made() - before,
    }


def scrape_catalog(inventory: dict[str, Any], out_root: Path, fetch: Fetch, *, only: set[str] | None = None,
                   refresh: bool = False, resume: bool = False, max_rounds: int | None = None,
                   requests_made: Callable[[], int] = lambda: 0,
                   now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> dict[str, Any]:
    """Procesa cada grupo de forma aislada: un fallo no detiene los demás; el informe se guarda tras cada grupo."""
    target_ids = {"season": inventory["season"]["external_id"], "game_type": inventory["game_type"]}
    groups: list[dict[str, Any]] = []
    report: dict[str, Any] = {"season": inventory["season"], "game_type": inventory["game_type"],
                              "started_at": now().isoformat(), "groups": groups}
    for comp, g in iter_groups(inventory, only):
        target = Target(target_ids["season"], comp["external_id"], g["external_id"], target_ids["game_type"])
        result = _scrape_group(target, group_dir(out_root, comp["external_id"], g["external_id"]), fetch,
                               g["expected_rounds"], refresh=refresh, resume=resume,
                               max_rounds=max_rounds, requests_made=requests_made)
        result.update(competition_id=comp["external_id"], competition_name=comp["name"],
                      group_id=g["external_id"], group_name=g["name"], expected_teams=g["expected_teams"])
        if result.get("teams_in_matches") is not None and g["expected_teams"] is not None:
            result["teams_match_expected"] = result["teams_in_matches"] == g["expected_teams"]
        groups.append(result)
        report["finished_at"] = now().isoformat()
        _write_report(out_root, report)
    report["finished_at"] = now().isoformat()
    _write_report(out_root, report)
    return report


def _write_report(out_root: Path, report: dict[str, Any]) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / REPORT_NAME).write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
