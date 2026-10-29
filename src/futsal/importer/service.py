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
from futsal.repositories.league import RegressionPolicy, Stats, import_plan


@dataclass
class ImportReport:
    status: str
    dry_run: bool
    rounds_processed: int = 0
    matches_processed: int = 0
    stats: Stats = field(default_factory=dict)
    issues: int = 0
    issue_codes: Counter[str] = field(default_factory=Counter)
    error: str | None = None
    run_id: int | None = None

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
        if self.issue_codes:
            out.append("incidencias por tipo: " + ", ".join(f"{k}={v}" for k, v in sorted(self.issue_codes.items())))
        out.append(f"registros: nuevos={self.total('inserted')} "
                   f"actualizados={self.total('updated')} sin cambios={self.total('unchanged')}")
        out.append(f"partidos: nuevos={m.get('inserted', 0)} actualizados={m.get('updated', 0)} "
                   f"sin cambios={m.get('unchanged', 0)} retenidos={m.get('held', 0)} omitidos={m.get('skipped', 0)}; "
                   f"observaciones nuevas={obs.get('inserted', 0)}")
        return out


DEGRADED_CODES = frozenset({"identity_conflict", "finished_regression_held"})


def _apply(session: Session, plan: ImportPlan, run: IngestionRun, now: datetime,
           policy: RegressionPolicy = RegressionPolicy()) -> Stats:
    stats = import_plan(session, plan, run.id, now, policy)
    for i in plan.issues:
        session.add(DataQualityIssue(ingestion_run_id=run.id, code=i.code,
                                     entity_type=i.entity_type, external_id=i.external_id,
                                     message=i.message, details=i.details))
    return stats


def _summarize(report: ImportReport, plan: ImportPlan) -> None:
    """Incidencias de la importación (incluidas las del guardarraíl) y estado: `partial` si algo no se pudo aplicar."""
    report.issues = len(plan.issues)
    report.issue_codes = Counter(i.code for i in plan.issues)
    if DEGRADED_CODES & report.issue_codes.keys():
        report.status = "partial"


def run_import(engine: Engine, input_path: Path, *, dry_run: bool = False,
               fail_on_quality_issues: bool = False, policy: RegressionPolicy = RegressionPolicy()) -> ImportReport:
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
            report.stats = _apply(session, plan, run, datetime.now(UTC), policy)
            session.rollback()
        _summarize(report, plan)
        return report

    started = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        run = IngestionRun(started_at=started, status="running", input_path=str(input_path), kind="matches")
        session.add(run)
        session.flush()
        run_id = run.id
    report.run_id = run_id
    try:
        with Session(engine) as session, session.begin():
            run = session.get_one(IngestionRun, run_id)
            report.stats = _apply(session, plan, run, started, policy)
            _summarize(report, plan)
            run.kind = "matches"
            run.status = report.status
            run.stats = {"issue_codes": dict(report.issue_codes),
                         "matches": {k: v for k, v in report.stats["matches"].items()}}
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
