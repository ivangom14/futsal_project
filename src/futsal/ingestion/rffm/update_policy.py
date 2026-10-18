"""Política de actualización incremental (`scrape-catalog --update`): qué jornadas YA descargadas conviene volver a pedir.

Decide solo a partir de los ficheros locales (JSON + HTML de la jornada): sin red. Una jornada se revisa si tiene partidos sin
resultado por una causa que puede cambiar (aplazado/suspendido, fecha pasada sin marcador, error de marcador o de acta en la primera
pasada, próximo partido) o si se cerró hace tan poco que aún puede corregirse. Las jornadas con todos los partidos cerrados y asentados se
omiten. Una jornada revisada hace poco (`min_age_hours`) no se vuelve a pedir, para no insistir a la web.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from futsal.ingestion.rffm.league import check_quality
from futsal.ingestion.rffm.models import RoundData

# anomalías de la primera pasada que justifican volver a pedir (errores de marcador, de acta o de datos de la jornada)
ANOMALY_CODES = frozenset({"non_numeric_score", "partial_score", "finished_without_score", "past_match_without_score",
                           "unknown_status", "empty_team", "same_team", "future_match_with_score", "match_without_id",
                           "empty_round"})
OPEN_STATUSES = frozenset({"scheduled", "postponed", "suspended", "unknown"})


@dataclass(frozen=True)
class UpdatePolicy:
    horizon_days: int = 14  # partidos por jugar dentro de este margen: pueden tener ya resultado, cambiar de hora o aplazarse
    settle_days: int = 3  # jornada cerrada: se vuelve a mirar hasta tener un snapshot tomado N días después de su último partido
    max_age_days: int = 30  # partidos lejanos: como mucho cada N días (detecta aplazamientos y cambios de calendario)
    min_age_hours: float = 12.0  # nunca se repite una jornada cuyo snapshot es más reciente que esto


def snapshot_time(html_path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(html_path.stat().st_mtime).astimezone()
    except OSError:
        return None


def classify_round(data: RoundData, html: str | None, snapshot_at: datetime | None, now: datetime,
                   policy: UpdatePolicy) -> str | None:
    """Motivo para volver a pedir la jornada, o None si se omite. El motivo es una etiqueta corta y estable."""
    today = now.date()
    if snapshot_at is not None and (now - snapshot_at) < timedelta(hours=policy.min_age_hours):
        return None
    if html is not None:
        try:
            codes = sorted({i["code"] for i in check_quality(data, html, today) if i["code"] in ANOMALY_CODES if i["code"]})
        except Exception:  # noqa: BLE001 - un HTML que ya no se puede analizar también se vuelve a pedir
            codes = ["html_ilegible"]
        if codes:
            return "anomalia:" + ",".join(codes)
    statuses = {m.status for m in data.matches}
    if statuses & {"postponed", "suspended"}:
        return "aplazado_o_suspendido"
    pending = [m for m in data.matches if m.status in OPEN_STATUSES]
    age_days = None if snapshot_at is None else (now - snapshot_at).days
    if pending:
        dates = [m.scheduled_date for m in pending if m.scheduled_date is not None]
        if len(dates) < len(pending):
            return "sin_fecha" if age_days is None or age_days >= policy.max_age_days else None
        soonest = min(dates)
        if soonest <= today + timedelta(days=policy.horizon_days):
            return "pendiente_proximo"
        return "lejano_caducado" if age_days is None or age_days >= policy.max_age_days else None
    last = _last_date(data)
    if last is not None and snapshot_at is not None and snapshot_at.date() < last + timedelta(days=policy.settle_days):
        return "cerrada_reciente"
    return None


def _last_date(data: RoundData) -> date | None:
    dates = [m.scheduled_date for m in data.matches if m.scheduled_date is not None]
    return max(dates) if dates else None
