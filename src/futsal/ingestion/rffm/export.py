"""Exportación JSON del resultado normalizado."""

from pathlib import Path

from futsal.ingestion.rffm.models import RoundData


def write_json(data: RoundData, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(data.model_dump_json(indent=2) + "\n", encoding="utf-8")
