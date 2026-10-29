"""Fase 21 sin PostgreSQL: barreras de calidad, estado global de la ejecución, alcance y umbral de abandono."""

import pytest

from futsal.refresh import gates, runs
from futsal.refresh.service import UnknownScope, filter_inventory, overall_status

IDS = ("22", "c1", "g1")


def _m(**kw: object) -> gates.GateResult:
    base: dict[str, object] = {"download_status": "downloaded", "download_error": None, "expected_ids": IDS, "got_ids": IDS,
                               "plan_matches": 14, "plan_rounds": 2, "stored_matches": 14, "stored_rounds": 2}
    base.update(kw)
    return gates.assess_matches(**base)  # type: ignore[arg-type]


def test_complete_download_is_applied_as_valid() -> None:
    r = _m()
    assert (r.decision, r.status) == ("apply", "success")


def test_empty_response_is_rejected_when_there_is_history_and_is_no_data_otherwise() -> None:
    assert (_m(plan_matches=0).decision, _m(plan_matches=0).status) == ("reject", "rejected")
    assert _m(plan_matches=0, stored_matches=0).status == "no_data"


def test_failures_and_unpublished_groups_are_not_confused() -> None:
    assert _m(download_status="failed", got_ids=None, download_error="HTTP 503").status == "failed"
    assert _m(download_status="no_rounds", got_ids=None).status == "no_data"


def test_wrong_identity_is_rejected() -> None:
    r = _m(got_ids=("22", "c1", "otro"))
    assert r.decision == "reject" and r.status == "rejected" and "identidad" in r.reasons[0]


@pytest.mark.parametrize("change", [{"download_status": "partial"}, {"plan_matches": 10}, {"plan_rounds": 1}])
def test_incomplete_download_is_applied_but_never_valid(change: dict[str, object]) -> None:
    r = _m(**change)
    assert (r.decision, r.status) == ("apply_partial", "partial") and r.reasons


def test_report_gate_rejects_only_structural_degradation() -> None:
    ok = dict(new_players=14, new_closed=True, stored_players=14, stored_closed=True)
    assert gates.assess_report(**ok).decision == "apply"
    assert gates.assess_report(new_players=0, new_closed=True, stored_players=14, stored_closed=True).decision == "reject"
    assert gates.assess_report(new_players=14, new_closed=False, stored_players=14, stored_closed=True).decision == "reject"
    assert gates.assess_report(new_players=0, new_closed=False, stored_players=None, stored_closed=None).decision == "apply"
    assert gates.assess_report(new_players=12, new_closed=True, stored_players=14, stored_closed=True).decision == "apply"  # corrección


@pytest.mark.parametrize("outcomes,expected", [
    ([], "no_changes"), (["no_changes", "no_changes"], "no_changes"), (["success", "no_changes"], "success"),
    (["success", "partial"], "partial"), (["success", "failed"], "partial"), (["failed", "rejected"], "failed"),
    (["failed", "no_data", "skipped"], "failed"), (["no_data", "skipped"], "no_changes"), (["partial"], "partial")])
def test_overall_status_never_reports_success_when_something_essential_failed(outcomes: list[str], expected: str) -> None:
    assert overall_status(outcomes) == expected


def _inv() -> dict[str, object]:
    return {"season": {"external_id": "22"}, "game_type": "3", "entries": [
        {"competition": {"external_id": "c1"}, "groups": [{"external_id": "g1"}, {"external_id": "g2"}]},
        {"competition": {"external_id": "c2"}, "groups": [{"external_id": "g3"}]}]}


def test_filter_inventory_selects_and_rejects_unknown_ids() -> None:
    out = filter_inventory(_inv(), None, {"g2"})  # type: ignore[arg-type]
    assert [g["external_id"] for e in out["entries"] for g in e["groups"]] == ["g2"]
    assert [e["competition"]["external_id"] for e in filter_inventory(_inv(), {"c2"}, None)["entries"]] == ["c2"]  # type: ignore[arg-type]
    with pytest.raises(UnknownScope):
        filter_inventory(_inv(), None, {"zzz"})  # type: ignore[arg-type]


def test_abandon_threshold_priority_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(runs.ENV_ABANDON_HOURS, raising=False)
    assert runs.abandon_hours() == runs.DEFAULT_ABANDON_HOURS == 6.0
    monkeypatch.setenv(runs.ENV_ABANDON_HOURS, "12")
    assert runs.abandon_hours() == 12.0 and runs.abandon_hours(2) == 2.0
    monkeypatch.setenv(runs.ENV_ABANDON_HOURS, "abc")
    with pytest.raises(ValueError):
        runs.abandon_hours()
    with pytest.raises(ValueError):
        runs.abandon_hours(0)
