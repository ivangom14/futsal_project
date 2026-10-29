"""Reconciliación de SOLO LECTURA: compara lo descargado de la fuente (snapshots locales) con lo almacenado en PostgreSQL.

Nunca escribe ni borra: la sesión se abre con `SET TRANSACTION READ ONLY` (lo impone la base de datos) y no hay red. Cada hallazgo se
clasifica en una de cinco categorías, porque no todo desacuerdo es un error de los datos almacenados:

- `confirmed_difference`: la fuente da un dato válido y distinto (lo corrige `refresh`).
- `possible_inconsistency`: no se puede decidir cuál es correcto (p. ej. regresión de un partido finalizado, identidad en otro grupo).
- `temporary_absence`: la fuente no devuelve información suficiente (jornada no descargada, acta aún no publicada).
- `ingest_error`: no se pudo interpretar la fuente (jornada ilegible, partido inválido).
- `historical`: el registro ya no aparece en una fuente completa, pero se conserva.
"""

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, time
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, aliased

from futsal.db.models import Competition, CompetitionGroup, Match, MatchReport, MatchReportObservation, Round, Team
from futsal.db.table_models import ScorerSnapshot, StandingsSnapshot
from futsal.importer.schema import LeagueFile, read_league
from futsal.importer.transform import SOURCE, MatchRec, build_plan
from futsal.refresh.freshness import latest_observation_times
from futsal.repositories.league import _closed_acta_score
from futsal.repositories.table_queries import choose_snapshot

CONFIRMED, POSSIBLE, TEMPORARY, INGEST_ERROR, HISTORICAL = (
    "confirmed_difference", "possible_inconsistency", "temporary_absence", "ingest_error", "historical")
CATEGORIES = (CONFIRMED, POSSIBLE, TEMPORARY, INGEST_ERROR, HISTORICAL)
MAX_LISTED = 50


@dataclass(frozen=True)
class Finding:
    category: str
    kind: str
    external_id: str | None = None
    field: str | None = None
    source: Any = None
    stored: Any = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _iso(value: date | time | None) -> str | None:
    return value.isoformat() if value is not None else None


def _is_regression(stored_status: str, m: MatchRec) -> bool:
    return stored_status == "finished" and (m.status in ("scheduled", "unknown") or (m.status == "finished" and m.home_score is None))


def _diffs(row: Match, rnd: str, home: str, away: str, m: MatchRec) -> list[tuple[str, Any, Any]]:
    pairs = [("status", m.status, row.status), ("home_score", m.home_score, row.home_score), ("away_score", m.away_score, row.away_score),
             ("scheduled_date", _iso(m.scheduled_date), _iso(row.scheduled_date)),
             ("scheduled_time", _iso(m.scheduled_time), _iso(row.scheduled_time)), ("venue", m.venue, row.venue),
             ("round", m.round_external_id, rnd), ("home_team", m.home_external_id, home), ("away_team", m.away_external_id, away)]
    return [(f, s, d) for f, s, d in pairs if s != d]


def _match_findings(db: Session, league: LeagueFile, gid: int, group_ext: str) -> list[Finding]:
    plan = build_plan(league)
    ok_rounds = {r.external_round_id for r in league.rounds if r.status == "ok"}
    out = [Finding(INGEST_ERROR, i.code, i.external_id, note=i.message) for i in plan.issues
           if i.code not in ("round_match_count",)]
    home, away = aliased(Team), aliased(Team)
    stored = {m.external_id: (m, r.external_id, h.external_id, a.external_id) for m, r, h, a in db.execute(
        select(Match, Round, home, away).join(Round, (Round.id == Match.round_id) & (Round.group_id == Match.group_id))
        .join(home, home.id == Match.home_team_id).join(away, away.id == Match.away_team_id).where(Match.group_id == gid))}
    source_ids = {m.external_id for m in plan.matches}
    for m in plan.matches:
        found = stored.get(m.external_id)
        if found is None:
            elsewhere = db.scalar(select(Match.group_id).where(Match.source == SOURCE, Match.external_id == m.external_id))
            if elsewhere is not None:
                out.append(Finding(POSSIBLE, "identity_conflict", m.external_id, note=f"el partido está en otro grupo (id {elsewhere})"))
            else:
                out.append(Finding(CONFIRMED, "missing_in_db", m.external_id, note="presente en la fuente y ausente en PostgreSQL"))
            continue
        row, rnd, h, a = found
        closed = _closed_acta_score(db, m.external_id)
        for field, src, dst in _diffs(row, rnd, h, a, m):
            if _is_regression(row.status, m) and field in ("status", "home_score", "away_score"):
                out.append(Finding(POSSIBLE, "finished_regression", m.external_id, field, src, dst,
                                   "la fuente devuelve como no jugado un partido finalizado: no se aplica sin confirmación"))
            elif closed is not None and field in ("home_score", "away_score"):
                out.append(Finding(POSSIBLE, "listing_vs_closed_report", m.external_id, field, src, dst,
                                   "el acta cerrada prevalece sobre el listado"))
            else:
                out.append(Finding(CONFIRMED, f"{field}_differs", m.external_id, field, src, dst))
    for ext, (row, rnd, _h, _a) in stored.items():
        if ext in source_ids:
            continue
        if rnd in ok_rounds:
            out.append(Finding(HISTORICAL, "absent_from_source", ext, note="la jornada se descargó completa y el partido ya no figura: se conserva"))
        else:
            out.append(Finding(TEMPORARY, "absent_round_not_downloaded", ext, note=f"la jornada {rnd} no se descargó correctamente"))
    return out


def _report_findings(db: Session, gid: int, report_dir: Path | None) -> list[Finding]:
    out: list[Finding] = []
    pending = db.execute(select(Match.external_id).outerjoin(MatchReport, MatchReport.match_id == Match.id).where(
        Match.group_id == gid, Match.status == "finished", MatchReport.id.is_(None)).order_by(Match.external_id)).scalars().all()
    out += [Finding(TEMPORARY, "report_pending", ext, note="partido finalizado sin acta incorporada") for ext in pending]
    if report_dir is None:
        return out
    for ext, rid in db.execute(select(Match.external_id, MatchReport.id).join(MatchReport, MatchReport.match_id == Match.id).where(
            Match.group_id == gid)):
        path = report_dir / ext / "normalized.json"
        if not path.exists():
            continue
        try:
            snap_hash = json.loads(path.read_text(encoding="utf-8"))["report"]["content_hash"]
        except (OSError, ValueError, KeyError):
            out.append(Finding(INGEST_ERROR, "report_snapshot_unreadable", ext, note=str(path)))
            continue
        current = db.scalar(select(MatchReportObservation.content_hash).where(MatchReportObservation.report_id == rid)
                            .order_by(MatchReportObservation.observed_at.desc(), MatchReportObservation.id.desc()).limit(1))
        if current != snap_hash:
            out.append(Finding(CONFIRMED, "report_changed", ext, "content_hash", snap_hash, current,
                               "el snapshot local del acta difiere de la versión vigente en PostgreSQL"))
    return out


def _table_findings(db: Session, gid: int) -> list[Finding]:
    out: list[Finding] = []
    times = latest_observation_times(db, gid)
    standings, st_latest = choose_snapshot(db, StandingsSnapshot, [StandingsSnapshot.group_id == gid], [
        StandingsSnapshot.round_number.desc(), StandingsSnapshot.last_seen_at.desc(), StandingsSnapshot.id.desc()])
    scorers, sc_latest = choose_snapshot(db, ScorerSnapshot, [ScorerSnapshot.group_id == gid],
                                         [ScorerSnapshot.last_seen_at.desc(), ScorerSnapshot.id.desc()])
    for kind, snap, latest in (("standings", standings, st_latest), ("scorers", scorers, sc_latest)):
        if snap is None:
            continue
        if snap.status != "ok":
            out.append(Finding(TEMPORARY, f"{kind}_not_validated", note=f"la captura servida está '{snap.status}'"))
        if latest is not None and latest.id != snap.id and latest.status != "ok" and latest.last_seen_at > snap.last_seen_at:
            out.append(Finding(TEMPORARY, f"{kind}_latest_attempt_{latest.status}",
                               note="el último intento fue parcial o vacío; se conserva la última captura válida"))
        newest = max([t for t in (times["finished"], times["report"]) if t is not None], default=None)
        if newest is not None and snap.fetched_at < newest:
            out.append(Finding(POSSIBLE, f"{kind}_older_than_results", note=(
                f"hay resultados observados ({newest.isoformat()}) posteriores a la captura ({snap.fetched_at.isoformat()}); "
                "no se recalcula: se actualiza con ingest-" + kind)))
    if isinstance(standings, StandingsSnapshot) and standings.current_round and standings.round_number < standings.current_round:
        out.append(Finding(POSSIBLE, "standings_behind_current_round", field="round", source=standings.current_round,
                           stored=standings.round_number))
    return out


def reconcile(engine: Engine, groups: list[tuple[str, str, Path | None]], *, report_dir: Path | None = None,
              max_listed: int = MAX_LISTED) -> dict[str, Any]:
    """`groups`: (ID competición, ID grupo, ruta de league.json o None) de la fuente. Devuelve el informe; NO modifica nada."""
    results: list[dict[str, Any]] = []
    with Session(engine) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))  # garantía a nivel de base de datos
        for comp_ext, group_ext, league_path in groups:
            row = db.execute(select(CompetitionGroup.id).join(Competition, Competition.id == CompetitionGroup.competition_id).where(
                CompetitionGroup.external_id == group_ext, Competition.external_id == comp_ext)).first()
            item: dict[str, Any] = {"competition_id": comp_ext, "group_id": group_ext, "league_path": str(league_path) if league_path else None}
            findings: list[Finding] = []
            if row is None:
                findings.append(Finding(CONFIRMED, "group_not_in_db", group_ext, note="el grupo no está importado en PostgreSQL"))
            else:
                gid = int(row[0])
                if league_path is None or not league_path.exists():
                    findings.append(Finding(TEMPORARY, "no_source_snapshot", note="no hay descarga local del grupo con la que comparar"))
                else:
                    try:
                        league = read_league(league_path)
                    except ValueError as exc:
                        findings.append(Finding(INGEST_ERROR, "league_unreadable", note=str(exc)[:200]))
                    else:
                        if (league.competition.external_id, league.group.external_id) != (comp_ext, group_ext):
                            findings.append(Finding(INGEST_ERROR, "identity_mismatch", note="el fichero no corresponde al grupo pedido"))
                        else:
                            findings += _match_findings(db, league, gid, group_ext)
                findings += _report_findings(db, gid, report_dir)
                findings += _table_findings(db, gid)
            counts = Counter(f.category for f in findings)
            kinds = Counter(f"{f.category}:{f.kind}" for f in findings)
            item.update(counts={c: counts.get(c, 0) for c in CATEGORIES}, kinds=dict(sorted(kinds.items())),
                        findings=[f.as_dict() for f in findings[:max_listed]], truncated=max(len(findings) - max_listed, 0))
            results.append(item)
        db.rollback()
    totals: Counter[str] = Counter()
    for r in results:
        totals.update(r["counts"])
    return {"read_only": True, "groups": results, "totals": {c: totals.get(c, 0) for c in CATEGORIES}}
