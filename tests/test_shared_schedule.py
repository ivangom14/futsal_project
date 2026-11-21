"""Fase 3E · programación COMPARTIDA de fútbol sala y fútbol 11 (deploy/systemd): un solo servicio, un solo script y los mismos
límites; la 2.ª pasada L-V a las 05:00 es una línea más del temporizador de entre semana, sin servicio propio de fútbol 11."""

import re
from pathlib import Path

SYSTEMD = Path(__file__).parents[1] / "deploy" / "systemd"


def _calendars(name: str) -> list[str]:
    return re.findall(r"^OnCalendar=(.+)$", (SYSTEMD / name).read_text(encoding="utf-8"), re.MULTILINE)


def test_weekday_timer_runs_at_03_and_05_on_the_shared_service() -> None:
    assert _calendars("futsito-update-weekday.timer") == ["Mon..Fri *-*-* 03:00:00 Europe/Madrid",
                                                          "Mon..Fri *-*-* 05:00:00 Europe/Madrid"]
    assert _calendars("futsito-update-weekend.timer") == ["Sat,Sun *-*-* *:00:00 Europe/Madrid"]  # sin cambios
    for timer in ("futsito-update-weekday.timer", "futsito-update-weekend.timer"):
        assert "Unit=futsito-update.service" in (SYSTEMD / timer).read_text(encoding="utf-8")


def test_one_service_same_limits_and_no_sport_specific_units() -> None:
    service = (SYSTEMD / "futsito-update.service").read_text(encoding="utf-8")
    assert "ExecStart=/opt/futsito/scripts/update_rffm.sh 900" in service and "TimeoutStartSec=58min" in service
    assert sorted(p.name for p in SYSTEMD.glob("*.service")) == ["futsito-update.service"]
    assert not [p for p in SYSTEMD.iterdir() if re.search(r"football|f11|futbol", p.name, re.IGNORECASE)]


def test_update_script_uses_one_tick_and_one_worker_for_both_sports() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "update_rffm.sh").read_text(encoding="utf-8")
    assert "flock -n 9" in script  # sin solapes entre la pasada de las 03:00 y la de las 05:00
    assert len(re.findall(r"ingest tick", script)) == 1 and len(re.findall(r"ingest work", script)) == 1
    assert "--game-type" not in script and "--max-reports" not in script  # el tick planifica los dos deportes; actas por alcance
