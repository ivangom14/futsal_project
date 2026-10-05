import json
from datetime import date, time
from pathlib import Path

import pytest

from futsal.importer.preview import render_preview, sanitize
from futsal.importer.schema import InputError, read_league
from futsal.importer.transform import build_plan, content_hash

FIXTURE = Path(__file__).parents[1] / "fixtures" / "league_two_rounds.json"
SNAPSHOT = Path(__file__).parents[1] / "fixtures" / "import_preview_two_rounds.txt"


def _mutated(tmp_path: Path, fn) -> Path:  # type: ignore[no-untyped-def]
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fn(data)
    p = tmp_path / "league.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_read_valid_fixture() -> None:
    plan = build_plan(read_league(FIXTURE))
    assert (len(plan.rounds), len(plan.matches), len(plan.teams), plan.issues) == (2, 14, 14, [])


def test_invalid_json_and_structure(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{no json", encoding="utf-8")
    with pytest.raises(InputError):
        read_league(bad)
    with pytest.raises(InputError):
        read_league(_mutated(tmp_path, lambda d: d.pop("rounds")))
    with pytest.raises(InputError):
        read_league(tmp_path / "missing.json")


def test_hash_stable_and_sensitive() -> None:
    a = content_hash("finished", date(2026, 9, 26), time(18, 0), 1, 2)
    assert a == content_hash("finished", date(2026, 9, 26), time(18, 0), 1, 2)
    assert a != content_hash("finished", date(2026, 9, 26), time(18, 0), 2, 1)
    assert a != content_hash("scheduled", date(2026, 9, 26), time(18, 0), 1, 2)
    assert a != content_hash("finished", date(2026, 9, 27), time(18, 0), 1, 2)


def test_transform_dedup_teams_and_scheduled_at() -> None:
    plan = build_plan(read_league(FIXTURE))
    assert len({t.external_id for t in plan.teams}) == len(plan.teams)
    with_time = next(m for m in plan.matches if m.scheduled_time)
    assert with_time.scheduled_at is not None and with_time.scheduled_at.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert all(len(m.content_hash) == 64 for m in plan.matches)


def test_partial_score_same_team_unknown_round(tmp_path: Path) -> None:
    def mutate(d: dict) -> None:  # type: ignore[type-arg]
        d["matches"][0]["home_score"] = 1
        d["matches"][0]["away_score"] = None
        d["matches"][1]["away_team_external_id"] = d["matches"][1]["home_team_external_id"]
        d["matches"][2]["external_round_id"] = "999"
    plan = build_plan(read_league(_mutated(tmp_path, mutate)))
    assert {i.code for i in plan.issues} == {"partial_score", "same_team", "unknown_round", "round_match_count"}
    assert len(plan.matches) == 11


def test_preview_snapshot() -> None:
    text = render_preview(build_plan(read_league(FIXTURE)))
    assert text == SNAPSHOT.read_text(encoding="utf-8")
    assert "html" not in text.lower() and "cookie" not in text.lower()


def test_sanitize_credentials() -> None:
    out = sanitize("postgresql://user:pw123@host/db token=abc Cookie: xyz")
    assert "pw123" not in out and "abc" not in out and "xyz" not in out
