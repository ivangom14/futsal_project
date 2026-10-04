"""Descubrimiento y descarga de todas las jornadas de la competición objetivo (sin red propia)."""

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from futsal.ingestion.rffm.client import FetchError, round_url
from futsal.ingestion.rffm.models import RoundData
from futsal.ingestion.rffm.parser import RffmParseError, extract_page_props, parse_round

Fetch = Callable[[str], str]
STATUSES = ("finished", "scheduled", "postponed", "suspended", "cancelled", "unknown")


class RoundRef(BaseModel):
    external_round_id: str  # valor del parámetro `jornada` (codjornada)
    visible_round_number: int | None
    visible_round_label: str
    scheduled_date: date | None
    source_url: str


@dataclass(frozen=True)
class Target:
    season: str
    competition: str
    group: str
    game_type: str


def _issue(code: str, detail: str, round_id: str | None = None, match_id: str | None = None) -> dict[str, str | None]:
    return {"code": code, "round_id": round_id, "match_id": match_id, "detail": detail}


def _dmy(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value).strip(), "%d-%m-%Y").date()
    except ValueError:
        return None


def discover_rounds(html: str, target: Target) -> tuple[list[RoundRef], list[dict[str, str | None]]]:
    """Lista real de jornadas desde `pageProps.rounds.jornadas` (o `results.listado_jornadas`)."""
    props = extract_page_props(html)
    raw: Any = (props.get("rounds") or {}).get("jornadas")
    if raw is None:
        listado = (props.get("results") or {}).get("listado_jornadas") or [{}]
        raw = listado[0].get("jornadas")
    if not isinstance(raw, list) or not raw:
        raise RffmParseError("No se encontró la lista de jornadas")
    refs: list[RoundRef] = []
    issues: list[dict[str, str | None]] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for item in raw:
        rid = str(item.get("codjornada") or "").strip() if isinstance(item, dict) else ""
        if not rid:
            issues.append(_issue("round_without_id", repr(item)[:120]))
            continue
        url = round_url(target.season, target.competition, target.group, rid, target.game_type)
        if rid in seen_ids or url in seen_urls:
            issues.append(_issue("duplicate_round", "ID o URL de jornada repetido", rid))
            continue
        seen_ids.add(rid)
        seen_urls.add(url)
        label = str(item.get("nombre") or rid).strip()
        refs.append(RoundRef(
            external_round_id=rid,
            visible_round_number=int(label) if label.isdigit() else None,
            visible_round_label=label,
            scheduled_date=_dmy(item.get("fecha_jornada")),
            source_url=url,
        ))
    return refs, issues


def check_identity(html: str, target: Target, round_id: str) -> list[str]:
    """Discrepancias entre la respuesta y el objetivo; vacío si todo coincide."""
    props = extract_page_props(html)
    results = props.get("results") or {}
    query = props.get("query") or {}
    found = {
        "temporada": ((props.get("season") or {}).get("cod_temporada"), target.season),
        "competicion": (results.get("codigo_competicion"), target.competition),
        "grupo": (results.get("codigo_grupo"), target.group),
        "tipojuego": (props.get("idGameType", query.get("tipojuego")), target.game_type),
        "jornada": (results.get("jornada"), round_id),
    }
    return [f"{k}: {got!r} != {want!r}" for k, (got, want) in found.items() if str(got) != want]


def check_quality(round_data: RoundData, html: str, today: date) -> list[dict[str, str | None]]:
    rid = round_data.external_round_id
    issues: list[dict[str, str | None]] = []
    if not round_data.matches:
        issues.append(_issue("empty_round", "jornada sin partidos", rid))
    raw = extract_page_props(html)["results"]["partidos"]
    for m, r in zip(round_data.matches, (p for p in raw if isinstance(p, dict)), strict=True):
        mid = m.external_match_id
        for key in ("Goles_casa", "Goles_visitante"):
            v = str(r.get(key) or "").strip()
            if v and not v.lstrip("-").isdigit():
                issues.append(_issue("non_numeric_score", f"{key}={v!r}", rid, mid))
        if not m.home_team_name or not m.away_team_name:
            issues.append(_issue("empty_team", "equipo local o visitante vacío", rid, mid))
        elif m.home_team_name == m.away_team_name or (
            m.home_team_external_id and m.home_team_external_id == m.away_team_external_id
        ):
            issues.append(_issue("same_team", "local igual a visitante", rid, mid))
        if (m.home_score is None) != (m.away_score is None):
            issues.append(_issue("partial_score", "solo un marcador informado", rid, mid))
        has_score = m.home_score is not None or m.away_score is not None
        if str(r.get("acta_cerrada") or "").strip() == "1" and not (
            m.home_score is not None and m.away_score is not None
        ):
            issues.append(_issue("finished_without_score", "acta cerrada sin marcador", rid, mid))
        future = m.scheduled_date is not None and m.scheduled_date > today
        if future and has_score:
            issues.append(_issue("future_match_with_score", "partido futuro con marcador", rid, mid))
        if m.status == "scheduled" and m.scheduled_date is not None and m.scheduled_date < today:
            issues.append(_issue("past_match_without_score", "fecha pasada sin marcador", rid, mid))
        if m.status == "unknown":
            issues.append(_issue("unknown_status", m.status_raw or "", rid, mid))
        if not mid:
            issues.append(_issue("match_without_id", "partido sin codacta", rid))
    return issues


def _write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True, default=str) + "\n"


def scrape_league(
    target: Target,
    out_dir: Path,
    fetch: Fetch,
    seed_round: str,
    *,
    refresh: bool = False,
    resume: bool = False,
    max_rounds: int | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    requests_made: Callable[[], int] = lambda: 0,
) -> dict[str, Any]:
    started = now()
    rounds_dir = out_dir / "rounds"
    fetched_now: set[str] = set()
    issues: list[dict[str, str | None]] = []
    failed: dict[str, str] = {}

    def html_path(rid: str) -> Path:
        return rounds_dir / f"{rid}.html"

    def json_path(rid: str) -> Path:
        return rounds_dir / f"{rid}.json"

    def get_html(rid: str) -> tuple[str, bool]:
        """(html, from_cache). Valida identidad antes de guardar un snapshot nuevo."""
        path = html_path(rid)
        if path.exists() and not (refresh and rid not in fetched_now):
            return path.read_text(encoding="utf-8"), rid not in fetched_now
        url = round_url(target.season, target.competition, target.group, rid, target.game_type)
        html = fetch(url)
        bad = check_identity(html, target, rid)
        if bad:
            raise RffmParseError("identidad distinta: " + "; ".join(bad))
        _write(path, html)
        fetched_now.add(rid)
        return html, False

    # Descubrimiento: la página semilla es una jornada válida y se reutiliza como snapshot.
    try:
        seed_html, _ = get_html(seed_round)
        refs, disc_issues = discover_rounds(seed_html, target)
    except (FetchError, RffmParseError) as exc:
        raise RuntimeError(f"No se pudo descubrir las jornadas: {exc}") from exc
    issues += disc_issues
    selected = refs[:max_rounds] if max_rounds is not None else refs

    today = now().date()
    results: dict[str, RoundData] = {}
    cached = 0
    for ref in selected:
        rid = ref.external_round_id
        try:
            if resume and json_path(rid).exists() and html_path(rid).exists() and rid not in fetched_now:
                data = RoundData.model_validate_json(json_path(rid).read_text(encoding="utf-8"))
                if (data.season_external_id, data.competition_external_id, data.group_external_id) != (
                    target.season, target.competition, target.group
                ) or data.external_round_id != rid:
                    raise RffmParseError("JSON de jornada con identidad distinta")
                from_cache = True
                html = html_path(rid).read_text(encoding="utf-8")
            else:
                html, from_cache = get_html(rid)
                bad = check_identity(html, target, rid)
                if bad:
                    raise RffmParseError("identidad distinta: " + "; ".join(bad))
                data = parse_round(html, ref.source_url, now())
                _write(json_path(rid), data.model_dump_json(indent=2) + "\n")
            issues += check_quality(data, html, today)
        except (FetchError, RffmParseError, ValueError) as exc:
            failed[rid] = str(exc)[:200]
            issues.append(_issue("round_failed", str(exc)[:200], rid))
            continue
        cached += from_cache
        results[rid] = data

    matches: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for ref in selected:
        data_r = results.get(ref.external_round_id)
        if data_r is None:
            continue
        for m in data_r.matches:
            mid = m.external_match_id
            if mid and mid in seen:
                duplicates.append(mid)
                same = seen[mid] == ref.external_round_id
                issues.append(_issue(
                    "duplicate_match" if same else "match_in_several_rounds",
                    f"primera aparición en jornada {seen[mid]}", ref.external_round_id, mid))
                continue
            if mid:
                seen[mid] = ref.external_round_id
            matches.append({"external_round_id": ref.external_round_id, **json.loads(m.model_dump_json())})

    counts = Counter(m["status"] for m in matches)
    finished = now()
    sample = next(iter(results.values()), None)
    league = {
        "competition": {"external_id": target.competition, "name": sample.competition_name if sample else None},
        "season": {"external_id": target.season},
        "group": {"external_id": target.group, "name": sample.group_name if sample else None},
        "game_type": {"external_id": target.game_type},
        "rounds": [
            {**json.loads(r.model_dump_json()),
             "matches": sum(1 for m in matches if m["external_round_id"] == r.external_round_id),
             "status": "failed" if r.external_round_id in failed else "ok"}
            for r in selected
        ],
        "matches": matches,
        "generated_at": finished.isoformat(),
        "source": "https://www.rffm.es/competicion/resultados-y-jornadas",
    }
    summary = {
        "rounds_discovered": len(refs),
        "rounds_selected": len(selected),
        "rounds_downloaded": len(results) - cached,
        "rounds_from_cache": cached,
        "rounds_failed": len(failed),
        "failed_rounds": failed,
        "http_requests": requests_made(),
        "matches_total": len(matches),
        **{f"matches_{s}": counts.get(s, 0) for s in STATUSES},
        "duplicate_match_ids": sorted(set(duplicates)),
        "quality_issues": issues,
        "rounds": [
            {"external_round_id": r.external_round_id, "visible_round_label": r.visible_round_label,
             "scheduled_date": r.scheduled_date.isoformat() if r.scheduled_date else None,
             "matches": sum(1 for m in matches if m["external_round_id"] == r.external_round_id),
             "failed": r.external_round_id in failed}
            for r in selected
        ],
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
    }
    _write(out_dir / "league.json", _dump(league))
    _write(out_dir / "scrape-summary.json", _dump(summary))
    return summary


def list_rounds(target: Target, html: str) -> list[RoundRef]:
    return discover_rounds(html, target)[0]

