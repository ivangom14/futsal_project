"""Orquestación: leer -> validar -> transformar -> transacción -> informe."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from futsal.db.models import DataQualityIssue, IngestionRun
from futsal.importer.schema import read_league
from futsal.importer.transform import ImportPlan, build_plan
from futsal.repositories.league import Stats, import_plan


@dataclass
class ImportReport:
    status: str
    dry_run: bool
    rounds_processed: int = 0
    matches_processed: int = 0
    stats: Stats = field(default_factory=dict)
    issues: int = 0
    error: str | None = None

    def total(self, key: str) -> int:
        return sum(c[key] for name, c in self.stats.items() if name != "observations")

    def lines(self) -> list[str]:
        m = self.stats.get("matches", Counter())
        obs = self.stats.get("observations", Counter())
        mode = "DRY-RUN (sin cambios persistidos)" if self.dry_run else "importación real"
        out = [f"{mode}: {self.status}"]
        if self.error:
            out.append(f"error: {self.error}")
        out.append(f"jornadas={self.rounds_processed} partidos={self.matches_processed} "
                   f"incidencias={self.issues}")
        out.append(f"registros: nuevos={self.total('inserted')} "
                   f"actualizados={self.total('updated')} sin cambios={self.total('unchanged')}")
        out.append(f"partidos: nuevos={m.get('inserted', 0)} actualizados={m.get('updated', 0)} "
                   f"sin cambios={m.get('unchanged', 0)}; "
                   f"observaciones nuevas={obs.get('inserted', 0)}")
        return out


def _apply(session: Session, plan: ImportPlan, run: IngestionRun, now: datetime) -> Stats:
    stats = import_plan(session, plan, run.id, now)
    for i in plan.issues:
        session.add(DataQualityIssue(ingestion_run_id=run.id, code=i.code,
                                     entity_type=i.entity_type, external_id=i.external_id,
                                     message=i.message))
    return stats


def run_import(engine: Engine, input_path: Path, *, dry_run: bool = False,
               fail_on_quality_issues: bool = False) -> ImportReport:
    plan = build_plan(read_league(input_path))
    report = ImportReport("success", dry_run, len(plan.rounds), len(plan.matches),
                          issues=len(plan.issues))
    if fail_on_quality_issues and plan.issues:
        report.status = "failed"
        report.error = f"{len(plan.issues)} incidencias de calidad (--fail-on-quality-issues)"
        return report

    if dry_run:  # misma lógica, transacción descartada: no persiste nada
        with Session(engine) as session:
            run = IngestionRun(started_at=datetime.now(UTC), status="running",
                               input_path=str(input_path))
            session.add(run)
            session.flush()
            report.stats = _apply(session, plan, run, datetime.now(UTC))
            session.rollback()
        return report

    started = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        run = IngestionRun(started_at=started, status="running", input_path=str(input_path))
        session.add(run)
        session.flush()
        run_id = run.id
    try:
        with Session(engine) as session, session.begin():
            run = session.get_one(IngestionRun, run_id)
            report.stats = _apply(session, plan, run, started)
            run.status = "success"
            run.finished_at = datetime.now(UTC)
            run.rounds_processed = report.rounds_processed
            run.matches_processed = report.matches_processed
            run.records_inserted = report.total("inserted")
            run.records_updated = report.total("updated")
            run.records_unchanged = report.total("unchanged")
            run.observations_created = report.stats["observations"]["inserted"]
            run.issues_created = len(plan.issues)
    except Exception as exc:
        report.status, report.error, report.stats = "failed", f"{type(exc).__name__}: {exc}"[:500], {}
        with Session(engine) as session, session.begin():
            run = session.get_one(IngestionRun, run_id)
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.error_summary = report.error
    return report
