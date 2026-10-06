"""Orquestación de actas: leer JSON normalizado -> transacción -> informe / resumen."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from futsal.db.models import (
    DataQualityIssue,
    IngestionRun,
    MatchEvent,
    MatchOfficial,
    MatchPlayer,
    MatchReport,
    MatchStaff,
    Player,
    PlayerTeamMembership,
)
from futsal.db.models import MatchReportObservation as Observation
from futsal.ingestion.rffm.report_compare import compare
from futsal.ingestion.rffm.report_models import ComparisonRow
from futsal.ingestion.rffm.report_models import MatchReport as ReportModel
from futsal.repositories.match_reports import (
    ReportImportError,
    Stats,
    import_report,
    load_match,
)

RUN_SOURCE = "rffm-match-report"


@dataclass
class ReportImportReport:
    status: str
    dry_run: bool
    stats: Stats = field(default_factory=dict)
    issues: list[tuple[str, str]] = field(default_factory=list)
    error: str | None = None

    def total(self, key: str) -> int:
        return sum(c[key] for c in self.stats.values())

    def rows(self) -> list[tuple[str, int, int, int]]:
        return [(t, c["inserted"], c["updated"], c["unchanged"])
                for t, c in self.stats.items() if c]

    def lines(self) -> list[str]:
        mode = "DRY-RUN (sin cambios persistidos)" if self.dry_run else "importación real"
        out = [f"{mode}: {self.status}"]
        if self.error:
            out.append(f"error: {self.error}")
        out += [f"{t}: nuevos={n} actualizados={u} sin cambios={k}" for t, n, u, k in self.rows()]
        out.append(f"incidencias={len(self.issues)}")
        return out


def read_report(path: Path) -> ReportModel:
    return ReportModel.model_validate_json(path.read_text(encoding="utf-8"))


def comparison_with_db(engine: Engine, report: ReportModel) -> list[ComparisonRow] | None:
    with Session(engine) as session:
        loaded = load_match(session, report.report.match_external_id)
    return None if loaded is None else compare(report, loaded.view)


def _apply(session: Session, report: ReportModel, run: IngestionRun, now: datetime
           ) -> tuple[Stats, list[tuple[str, str]]]:
    stats, issues = import_report(session, report, run.id, now)
    for code, message in issues:
        session.add(DataQualityIssue(ingestion_run_id=run.id, code=code, entity_type="match_report",
                                     external_id=report.report.report_external_id, message=message))
    return stats, issues


def run_report_import(engine: Engine, input_path: Path, *, dry_run: bool = False
                      ) -> ReportImportReport:
    report = read_report(input_path)
    out = ReportImportReport("success", dry_run)
    now = datetime.now(UTC)
    if dry_run:
        with Session(engine) as session:
            run = IngestionRun(source=RUN_SOURCE, started_at=now, status="running",
                               input_path=str(input_path))
            session.add(run)
            session.flush()
            try:
                out.stats, out.issues = _apply(session, report, run, now)
            except ReportImportError as exc:
                out.status, out.error = "failed", str(exc)
            session.rollback()
        return out
    with Session(engine) as session, session.begin():
        run = IngestionRun(source=RUN_SOURCE, started_at=now, status="running",
                           input_path=str(input_path))
        session.add(run)
        session.flush()
        run_id = run.id
    try:
        with Session(engine) as session, session.begin():
            run = session.get_one(IngestionRun, run_id)
            out.stats, out.issues = _apply(session, report, run, now)
            run.status, run.finished_at, run.matches_processed = "success", datetime.now(UTC), 1
            run.records_inserted = out.total("inserted")
            run.records_updated = out.total("updated")
            run.records_unchanged = out.total("unchanged")
            run.observations_created = out.stats["match_report_observations"]["inserted"]
            run.issues_created = len(out.issues)
    except Exception as exc:  # noqa: BLE001 - se registra y se informa; la transacción ya hizo rollback
        out.status, out.error, out.stats = "failed", f"{type(exc).__name__}: {exc}"[:500], {}
        with Session(engine) as session, session.begin():
            run = session.get_one(IngestionRun, run_id)
            run.status, run.finished_at, run.error_summary = "failed", datetime.now(UTC), out.error
    return out


def report_summary(engine: Engine) -> dict[str, Any]:
    models = {"match_reports": MatchReport, "match_report_observations": Observation,
              "players": Player, "player_team_memberships": PlayerTeamMembership,
              "match_players": MatchPlayer, "match_staff": MatchStaff,
              "match_officials": MatchOfficial, "match_events": MatchEvent}
    with Session(engine) as s:
        summary: dict[str, Any] = {
            k: s.scalar(select(func.count()).select_from(m)) for k, m in models.items()}
        summary["quality_issues"] = s.scalar(select(func.count()).select_from(DataQualityIssue).where(
            DataQualityIssue.entity_type == "match_report"))
        runs = s.scalars(select(IngestionRun).where(IngestionRun.source == RUN_SOURCE)
                         .order_by(IngestionRun.id).limit(2)).all()
        for label, run in zip(("first_import", "second_import"), runs, strict=False):
            summary[label] = {
                "status": run.status, "records_inserted": run.records_inserted,
                "records_updated": run.records_updated, "records_unchanged": run.records_unchanged,
                "observations_created": run.observations_created,
                "issues_created": run.issues_created}
    return summary
