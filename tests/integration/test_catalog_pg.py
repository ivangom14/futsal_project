"""Persistencia de varias competiciones y grupos (datos simulados derivados del fixture de liga)."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from futsal.api.app import create_app
from futsal.db.models import Competition, CompetitionGroup, Match, Team
from futsal.importer.catalog_service import import_catalog, verify_catalog
from futsal.importer.service import run_import

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures" / "league_two_rounds.json"


def variant(tmp_path: Path, name: str, comp: str, group: str, salt: str, comp_name: str, drop: int = 0) -> Path:
    """Copia del fixture con otra competición/grupo; mismos nombres de equipo, IDs externos distintos."""
    d: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    d["competition"].update(external_id=comp, name=comp_name)
    d["group"].update(external_id=group, name=f"Grupo {salt}")
    for m in d["matches"]:
        m["external_match_id"] = salt + m["external_match_id"]
        m["home_team_external_id"] = salt + m["home_team_external_id"]
        m["away_team_external_id"] = salt + m["away_team_external_id"]
    d["matches"] = d["matches"][drop:]
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    return path


def report_for(paths: dict[tuple[str, str], Path], statuses: dict[tuple[str, str], str] | None = None) -> dict[str, Any]:
    groups = []
    for (comp, grp), path in paths.items():
        try:
            total: int | None = len(json.loads(path.read_text())["matches"])
        except json.JSONDecodeError:
            total = None
        groups.append({"competition_id": comp, "group_id": grp, "status": (statuses or {}).get((comp, grp), "downloaded"),
                       "league_path": str(path), "matches_total": total})
    return {"groups": groups}


@pytest.fixture()
def paths(tmp_path: Path) -> dict[tuple[str, str], Path]:
    return {("900", "901"): variant(tmp_path, "a", "900", "901", "7", "COMP A"),
            ("900", "902"): variant(tmp_path, "b", "900", "902", "8", "COMP A", drop=2),
            ("910", "911"): variant(tmp_path, "c", "910", "911", "9", "COMP B")}


def test_several_groups_persist_with_clean_relations(engine: Engine, paths: dict[tuple[str, str], Path]) -> None:
    results = import_catalog(engine, report_for(paths))
    assert [r["import_status"] for r in results] == ["success"] * 3
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Competition)) == 2
        assert s.scalar(select(func.count()).select_from(CompetitionGroup)) == 3
        # los mismos nombres de equipo en grupos distintos son filas distintas (unicidad por grupo)
        names = s.scalars(select(Team.name).where(Team.name.like("%SIMANCAS 'B'%"))).all()
        assert len(names) == 3
        per_group = dict(s.execute(select(Match.group_id, func.count()).group_by(Match.group_id)).all())
        assert sorted(per_group.values()) == [12, 14, 14]
    out = verify_catalog(engine, report_for(paths))
    assert not any(out["integrity"].values())
    assert all(g["persisted"] and g["matches_match_download"] for g in out["groups"])


def test_repeating_import_creates_no_duplicates(engine: Engine, paths: dict[tuple[str, str], Path]) -> None:
    import_catalog(engine, report_for(paths))
    again = import_catalog(engine, report_for(paths))
    assert all(r["inserted"] == 0 and r["updated"] == 0 for r in again)
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Match)) == 40
    assert not any(verify_catalog(engine, report_for(paths))["integrity"].values())


def test_failed_and_partial_groups_are_traced(engine: Engine, paths: dict[tuple[str, str], Path]) -> None:
    statuses = {("900", "901"): "failed", ("900", "902"): "partial"}
    results = {r["group_id"]: r for r in import_catalog(engine, report_for(paths, statuses))}
    assert results["901"]["import_status"] == "skipped" and "failed" in results["901"]["error"]
    assert results["902"]["import_status"] == "success" and results["911"]["import_status"] == "success"
    out = {g["group_id"]: g for g in verify_catalog(engine, report_for(paths, statuses))["groups"]}
    assert out["901"]["persisted"] is False and out["902"]["download_status"] == "partial"


def test_unreadable_league_file_does_not_stop_the_batch(engine: Engine, paths: dict[tuple[str, str], Path],
                                                         tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{no json", encoding="utf-8")
    paths[("900", "901")] = bad
    results = {r["group_id"]: r["import_status"] for r in import_catalog(engine, report_for(paths))}
    assert results == {"901": "failed", "902": "success", "911": "success"}


def test_initial_competition_is_untouched_and_api_scopes_by_group(engine: Engine, paths: dict[tuple[str, str], Path]) -> None:
    run_import(engine, FIXTURE)
    import_catalog(engine, report_for(paths))
    with TestClient(create_app(engine)) as client:
        comps = {c["external_id"]: c for c in client.get("/competitions").json()["items"]}
        assert set(comps) == {"26738243", "900", "910"}
        initial = client.get(f"/competitions/{comps['26738243']['id']}/groups").json()["items"]
        assert [g["external_id"] for g in initial] == ["26738245"]
        groups = client.get(f"/competitions/{comps['900']['id']}/groups").json()["items"]
        assert [g["external_id"] for g in groups] == ["901", "902"]
        counts = {g["external_id"]: client.get(f"/groups/{g['id']}/matches").json()["count"] for g in groups}
        assert counts == {"901": 14, "902": 12}
        gid = initial[0]["id"]
        assert client.get(f"/groups/{gid}/matches").json()["count"] == 14
        teams = {g["external_id"]: {t["id"] for t in client.get(f"/groups/{g['id']}/teams").json()["items"]} for g in groups}
        assert teams["901"].isdisjoint(teams["902"])
        # un equipo de otro grupo no es válido en este grupo
        other = next(iter(teams["902"]))
        assert client.get(f"/groups/{groups[0]['id']}/matches?team_id={other}").status_code == 404


def test_competitions_list_their_groups_and_teams_resolve_across_groups(engine: Engine,
                                                                        paths: dict[tuple[str, str], Path]) -> None:
    import_catalog(engine, report_for(paths))
    with TestClient(create_app(engine)) as client:
        comps = {c["external_id"]: c for c in client.get("/competitions").json()["items"]}
        assert [g["name"] for g in comps["900"]["groups"]] == ["Grupo 7", "Grupo 8"]
        assert len(comps["910"]["groups"]) == 1
        found = client.get("/teams", params={"name": "simancas"}).json()
        assert found["count"] == 3  # el mismo club en tres grupos: el cliente debe desambiguar
        assert {t["group_id"] for t in found["items"]} == {g["id"] for c in comps.values() for g in c["groups"]}
        assert all({"competition", "group", "group_id", "competition_id"} <= set(t) for t in found["items"])
        assert client.get("/teams", params={"name": "simancas", "limit": 2}).json()["shown"] == 2
        assert client.get("/teams", params={"name": "%%"}).json()["count"] == 0  # comodines escapados
        assert client.get("/teams", params={"name": "x"}).status_code == 400
