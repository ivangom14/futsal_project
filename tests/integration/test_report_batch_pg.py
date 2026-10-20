import json
from pathlib import Path

import pytest
from sqlalchemy import Engine

from futsal.importer.report_service import report_summary
from futsal.importer.service import run_import
from futsal.ingestion.rffm.client import FetchError
from futsal.ingestion.rffm.progress import Progress
from futsal.report_batch import list_candidates, plan_reports, process_reports

pytestmark = pytest.mark.integration
FIX = Path(__file__).parents[1] / "fixtures"
HTML = (FIX / "rffm_match_report.html").read_text(encoding="utf-8")


@pytest.fixture()
def seeded(engine: Engine) -> Engine:
    assert run_import(engine, FIX / "league_two_rounds.json").status == "success"
    return engine


def test_identity_mismatch_is_isolated_and_never_cached(seeded: Engine, tmp_path: Path) -> None:
    urls: list[str] = []

    def fake(url: str) -> str:  # siempre devuelve el acta 5575697, también para otros partidos
        urls.append(url)
        return HTML

    s = process_reports(seeded, tmp_path, fake, limit=3, requests_made=lambda: len(urls))
    by_id = {m["match_id"]: m for m in s["matches"]}
    assert s["processed"] == 3 and s["imported"] == 1 and s["failed"] == 2
    assert by_id["5575697"]["status"] == "imported" and s["http_requests"] == 3
    failed = [m for m in s["matches"] if m["status"] == "failed"]
    assert all("identidad distinta" in m["error"] for m in failed)
    assert not any((tmp_path / m["match_id"] / "raw.html").exists() for m in failed)
    assert s["database"]["match_reports"] == 1 and s["database"]["players"] == 21


def test_cache_first_and_rerun_has_no_candidates_left(seeded: Engine, tmp_path: Path) -> None:
    (tmp_path / "5575697").mkdir()
    (tmp_path / "5575697" / "raw.html").write_text(HTML, encoding="utf-8")

    def forbidden(url: str) -> str:
        raise AssertionError("no debe descargar si hay snapshot")

    s = process_reports(seeded, tmp_path, forbidden, limit=1)
    assert (s["from_cache"], s["downloaded"], s["imported"], s["http_requests"]) == (1, 0, 1, 0)
    asked: list[str] = []

    def other(url: str) -> str:
        asked.append(url)
        return HTML  # identidad distinta -> falla aislado

    again = process_reports(seeded, tmp_path, other, limit=None)
    assert again["candidates"] == 12 and again["imported"] == 0
    assert not any("/5575697?" in u for u in asked)  # lo ya importado no se pide otra vez
    assert report_summary(seeded)["match_report_observations"] == 1


def test_aborts_after_consecutive_fetch_failures(seeded: Engine, tmp_path: Path) -> None:
    calls: list[str] = []

    def blocked(url: str) -> str:
        calls.append(url)
        raise FetchError("HTTP 403")

    s = process_reports(seeded, tmp_path, blocked, limit=None, requests_made=lambda: len(calls))
    assert s["processed"] == 3 and s["aborted"] and s["failed"] == 3 and len(calls) == 3


# --- Fase 14.3: actas de varios grupos ----------------------------------------------------------------------------------

OTHER_GROUP = "26738244"


@pytest.fixture()
def two_groups(seeded: Engine, tmp_path: Path) -> Engine:
    """Segundo grupo de la misma competición: mismos nombres, IDs de grupo, equipo y partido distintos."""
    data = json.loads((FIX / "league_two_rounds.json").read_text(encoding="utf-8"))
    data["group"] = {"external_id": OTHER_GROUP, "name": "Grupo 1"}
    for m in data["matches"]:
        m["external_match_id"] = "9" + m["external_match_id"]
        for k in ("home_team_external_id", "away_team_external_id"):
            m[k] = "9" + m[k]
    path = tmp_path / "other_group.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert run_import(seeded, path).status == "success"
    return seeded


def test_default_scope_is_still_the_initial_group_only(two_groups: Engine, tmp_path: Path) -> None:
    initial = list_candidates(two_groups, ("26738245",), None)
    assert initial and {c.group_external_id for c in initial} == {"26738245"}
    asked: list[str] = []

    def web(url: str) -> str:
        asked.append(url)
        return HTML

    s = process_reports(two_groups, tmp_path, web, limit=None, requests_made=lambda: len(asked))   # sin `groups`: comportamiento anterior
    assert s["groups"] == ["26738245"] and set(s["candidates_by_group"]) == {"26738245"}
    assert not any(OTHER_GROUP in u for u in asked)


def test_all_groups_and_specific_group_select_the_right_matches(two_groups: Engine, tmp_path: Path) -> None:
    everything = list_candidates(two_groups, None, None)
    only_other = list_candidates(two_groups, [OTHER_GROUP], None)
    assert {c.group_external_id for c in everything} == {"26738245", OTHER_GROUP}
    assert {c.group_external_id for c in only_other} == {OTHER_GROUP} and all(c.external_id.startswith("9") for c in only_other)
    assert len(everything) == len(list_candidates(two_groups, ["26738245"], None)) + len(only_other)
    both = list_candidates(two_groups, ["26738245", OTHER_GROUP], None)
    assert [c.group_external_id for c in both] == sorted(c.group_external_id for c in both)   # ordenados por grupo
    assert len(list_candidates(two_groups, None, 3)) == 3                                    # --limit se respeta


def test_plan_lists_candidates_per_group_without_network_and_counts_cached(two_groups: Engine, tmp_path: Path) -> None:
    first = list_candidates(two_groups, [OTHER_GROUP], None)[0]
    (tmp_path / first.external_id).mkdir()
    (tmp_path / first.external_id / "raw.html").write_text(HTML, encoding="utf-8")
    plan = plan_reports(two_groups, tmp_path, None, None)
    assert set(plan["by_group"]) == {"26738245", OTHER_GROUP} and plan["candidates"] == sum(plan["by_group"].values())
    assert plan["from_cache"] == 1 and plan["estimated_requests"] == plan["candidates"] - 1


def test_other_group_report_is_downloaded_with_its_own_url_identity_checked_and_progress_shown(two_groups: Engine, tmp_path: Path) -> None:
    import io

    target = list_candidates(two_groups, [OTHER_GROUP], 1)[0]
    asked: list[str] = []

    def web(url: str) -> str:
        asked.append(url)
        return HTML                                              # el acta de otro partido/grupo: debe rechazarse

    out = io.StringIO()
    progress = Progress(0, 1, lambda: len(asked), out=out, live=False, group_word="lote", item_word="acta")
    s = process_reports(two_groups, tmp_path, web, limit=1, groups=[OTHER_GROUP], requests_made=lambda: len(asked), progress=progress)
    assert s["groups"] == [OTHER_GROUP] and s["candidates_by_group"] == {OTHER_GROUP: 1}
    assert len(asked) == 1 and OTHER_GROUP in asked[0] and target.external_id in asked[0]     # URL del grupo y partido correctos
    assert s["failed"] == 1 and s["failed_by_group"] == {OTHER_GROUP: 1} and "identidad distinta" in s["matches"][0]["error"]
    assert not (tmp_path / target.external_id / "raw.html").exists()                             # nunca se guarda otra página
    assert "[lote 1/1] actas" in out.getvalue() and "acta 1/1" in out.getvalue() and "peticiones 1/~1" in out.getvalue()

