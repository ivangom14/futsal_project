"""Fase 21: `refresh` de extremo a extremo con fixtures y PostgreSQL desechable (sin red real).

Los partidos se leen del snapshot local (`offline=True`) o de un `fetch` simulado; las tablas, del sitio RFFM simulado de las pruebas de la Fase 16.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from futsal.db.models import (
    Competition,
    CompetitionGroup,
    DatasetRefresh,
    IngestionRun,
    Match,
    MatchReportObservation,
    Season,
    Team,
)
from futsal.db.table_models import ScorerSnapshot, StandingsSnapshot
from futsal.ingestion.rffm.catalog_ingest import group_dir
from futsal.ingestion.rffm.client import FetchError
from futsal.refresh.freshness import group_freshness
from futsal.refresh.service import RefreshOptions, UnknownScope, run_refresh
from helpers_tables import FakeSite, load

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).parents[1] / "fixtures"
FIXTURE = FIXTURES / "league_two_rounds.json"
COMP, GROUP, SEASON = "26738243", "26738245", "22"
NOW = datetime(2099, 1, 1, 12, 0, tzinfo=UTC)


def _inventory(groups: list[tuple[str, str]] | None = None, expected_rounds: int = 2) -> dict[str, Any]:
    groups = groups or [(COMP, GROUP)]
    entries: dict[str, dict[str, Any]] = {}
    for comp, grp in groups:
        e = entries.setdefault(comp, {"identification": "confirmed", "groups_status": "ok",
                                      "competition": {"external_id": comp, "name": "COMPETICION"}, "groups": []})
        e["groups"].append({"external_id": grp, "name": f"Grupo {grp}", "expected_rounds": expected_rounds, "expected_teams": 14})
    return {"season": {"external_id": SEASON, "name": "2026-27"}, "game_type": "3", "entries": list(entries.values())}


def _league(**changes: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for mid, change in changes.items():
        for m in data["matches"]:
            if m["external_match_id"] == mid:
                m.update(change)
    return data


def _put(work: Path, data: dict[str, Any], comp: str = COMP, grp: str = GROUP) -> Path:
    path = group_dir(work, comp, grp) / "league.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _other_group(grp: str, prefix: str) -> dict[str, Any]:
    data = _league()
    data["group"] = {"external_id": grp, "name": f"Grupo {grp}"}
    for m in data["matches"]:
        m["external_match_id"] = prefix + m["external_match_id"]
    return data


def _refresh(engine: Engine, work: Path, tmp_path: Path, *, inv: dict[str, Any] | None = None, fetch: Any = None,
             datasets: tuple[str, ...] = ("matches",), **kw: Any) -> dict[str, Any]:
    opts = kw.pop("opts", None) or RefreshOptions(datasets=datasets, offline=kw.pop("offline", True), dry_run=kw.pop("dry_run", False),
                                                  mode=kw.pop("mode", "incremental"))
    return run_refresh(engine, inv or _inventory(), work, tmp_path / "tables", tmp_path / "reports", fetch or FakeSite({}), opts,
                       now=lambda: NOW, **kw)


def _count(engine: Engine, model: Any) -> int:
    with Session(engine) as s:
        return int(s.scalar(select(func.count()).select_from(model)) or 0)


def _run(engine: Engine, run_id: int) -> IngestionRun:
    with Session(engine) as s:
        run = s.get_one(IngestionRun, run_id)
        s.expunge(run)
        return run


def _dataset(engine: Engine, dataset: str = "matches", group_ext: str = GROUP) -> DatasetRefresh:
    with Session(engine) as s:
        row = s.scalars(select(DatasetRefresh).join(CompetitionGroup, CompetitionGroup.id == DatasetRefresh.group_id).where(
            CompetitionGroup.external_id == group_ext, DatasetRefresh.dataset == dataset)).one()
        s.expunge(row)
        return row


# ------------------------------------------------------------------------------------------ idempotencia (F21-01/02/03/04)
def test_first_run_inserts_second_run_changes_nothing(engine: Engine, tmp_path: Path) -> None:
    work = tmp_path / "work"
    _put(work, _league())
    first = _refresh(engine, work, tmp_path)
    assert first["status"] == "success" and first["groups"][0]["datasets"]["matches"]["counts"]["inserted"] > 0
    assert _count(engine, Match) == 14
    row1 = _dataset(engine)
    second = _refresh(engine, work, tmp_path)
    assert second["status"] == "no_changes"
    c = second["groups"][0]["datasets"]["matches"]["counts"]
    assert (c["inserted"], c["updated"], c["observations"]) == (0, 0, 0) and c["unchanged"] > 0
    assert _count(engine, Match) == 14 and _count(engine, Team) == 14
    run = _run(engine, second["run_id"])
    assert run.status == "no_changes" and run.kind == "refresh" and run.finished_at and run.stats["scope"]["datasets"] == ["matches"]
    assert _dataset(engine).last_valid_at >= row1.last_valid_at


def test_source_changes_update_existing_matches_without_duplicates(engine: Engine, tmp_path: Path) -> None:
    work = tmp_path / "work"
    _put(work, _league())
    _refresh(engine, work, tmp_path)
    changed = _league(**{"5575696": {"scheduled_date": "2027-02-02"}, "5575697": {"home_score": 5, "away_score": 4}})
    _put(work, changed)
    out = _refresh(engine, work, tmp_path)
    counts = out["groups"][0]["datasets"]["matches"]["counts"]
    assert out["status"] == "success" and counts["updated"] == 2 and counts["inserted"] == 0
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Match)) == 14
        moved = s.scalars(select(Match).where(Match.external_id == "5575696")).one()
        assert str(moved.scheduled_date) == "2027-02-02"
        fixed = s.scalars(select(Match).where(Match.external_id == "5575697")).one()
        assert (fixed.home_score, fixed.away_score) == (5, 4)


def test_new_matches_in_an_incremental_update_are_incorporated(engine: Engine, tmp_path: Path) -> None:
    work = tmp_path / "work"
    partial = _league()
    partial["matches"] = [m for m in partial["matches"] if m["external_round_id"] == "1"]
    partial["rounds"] = [r for r in partial["rounds"] if r["external_round_id"] == "1"]
    _put(work, partial)
    _refresh(engine, work, tmp_path, inv=_inventory(expected_rounds=1))
    assert _count(engine, Match) == 7
    _put(work, _league())
    out = _refresh(engine, work, tmp_path)
    assert out["status"] == "success" and _count(engine, Match) == 14


# ---------------------------------------------------------------------------- información incompleta (F21-07/08)
def test_empty_response_does_not_erase_data_or_advance_freshness(engine: Engine, tmp_path: Path) -> None:
    work = tmp_path / "work"
    _put(work, _league())
    _refresh(engine, work, tmp_path)
    valid_before = _dataset(engine).last_valid_at
    empty = _league()
    empty["matches"] = []
    _put(work, empty)
    later = datetime(2099, 1, 2, tzinfo=UTC)
    out = run_refresh(engine, _inventory(), work, tmp_path / "t", tmp_path / "r", FakeSite({}),
                      RefreshOptions(datasets=("matches",), offline=True), now=lambda: later)
    m = out["groups"][0]["datasets"]["matches"]
    assert m["status"] == "rejected" and out["status"] == "failed" and "se conservan" in m["message"]
    assert _count(engine, Match) == 14
    row = _dataset(engine)
    assert row.last_attempt_status == "rejected" and row.last_valid_at == valid_before and row.last_attempt_at == later
    assert _run(engine, out["run_id"]).status == "failed"


def test_partial_response_keeps_history_and_is_not_a_valid_update(engine: Engine, tmp_path: Path) -> None:
    work = tmp_path / "work"
    _put(work, _league())
    _refresh(engine, work, tmp_path)
    valid_before = _dataset(engine).last_valid_at
    partial = _league()
    partial["matches"] = partial["matches"][:5]
    partial["rounds"][0]["matches"], partial["rounds"][1]["matches"] = 5, 0
    _put(work, partial)
    later = datetime(2099, 1, 3, tzinfo=UTC)
    out = run_refresh(engine, _inventory(), work, tmp_path / "t", tmp_path / "r", FakeSite({}),
                      RefreshOptions(datasets=("matches",), offline=True), now=lambda: later)
    m = out["groups"][0]["datasets"]["matches"]
    assert m["status"] == "partial" and out["status"] == "partial" and "14 almacenados" in m["message"]
    assert _count(engine, Match) == 14  # nada se borra
    assert _dataset(engine).last_valid_at == valid_before and _dataset(engine).last_attempt_status == "partial"


def test_http_error_timeout_and_unparseable_page_preserve_data(engine: Engine, tmp_path: Path) -> None:
    work = tmp_path / "work"
    _put(work, _league())
    _refresh(engine, work, tmp_path)
    for failure in (FetchError("HTTP 503"), FetchError("sin respuesta válida tras 3 intentos: TimeoutException()"),
                    None):  # None: HTML sin __NEXT_DATA__ (la estructura cambió)
        class Site:
            requests = 0

            def __call__(self, url: str, f: Exception | None = failure) -> str:
                if f is not None:
                    raise f
                return "<html><body>mantenimiento</body></html>"

        out = _refresh(engine, work, tmp_path, offline=False, fetch=Site(), mode="full")
        assert out["groups"][0]["datasets"]["matches"]["status"] == "failed" and out["status"] == "failed"
        assert _count(engine, Match) == 14
    assert _dataset(engine).last_attempt_status == "failed"


def test_file_of_another_group_is_rejected(engine: Engine, tmp_path: Path) -> None:
    work = tmp_path / "work"
    wrong = _league()
    wrong["group"] = {"external_id": "999", "name": "otro"}
    _put(work, wrong)
    out = _refresh(engine, work, tmp_path)
    assert out["groups"][0]["datasets"]["matches"]["status"] == "rejected" and _count(engine, Match) == 0


def test_group_not_yet_published_is_not_an_error(engine: Engine, tmp_path: Path) -> None:
    out = _refresh(engine, tmp_path / "work", tmp_path)  # sin descarga local
    assert out["groups"][0]["datasets"]["matches"]["status"] == "no_data" and out["status"] == "no_changes"


# ------------------------------------------------------------------------- aislamiento y alcance (F21-05/06/15)
def test_selective_update_never_touches_other_groups(engine: Engine, tmp_path: Path) -> None:
    work = tmp_path / "work"
    other = "26738244"
    inv = _inventory([(COMP, GROUP), (COMP, other)])
    _put(work, _league())
    _put(work, _other_group(other, "9"), grp=other)
    _refresh(engine, work, tmp_path, inv=inv)
    assert _count(engine, Match) == 28
    changed_a = _league(**{"5575697": {"home_score": 1, "away_score": 1}})
    _put(work, changed_a)
    changed_b = _other_group(other, "9")
    changed_b["matches"][0]["venue"] = "NUEVO CAMPO"
    _put(work, changed_b, grp=other)
    out = _refresh(engine, work, tmp_path, inv=inv, groups={other})
    assert [g["group_id"] for g in out["groups"]] == [other]
    with Session(engine) as s:
        a = s.scalars(select(Match).where(Match.external_id == "5575697")).one()
        assert (a.home_score, a.away_score) == (3, 4)  # el grupo A no se tocó, aunque su snapshot ya cambió
        b = s.scalars(select(Match).where(Match.external_id == "95575696")).one()
        assert b.venue == "NUEVO CAMPO"
        assert s.scalar(select(func.count()).select_from(Match).where(Match.group_id == b.group_id)) == 14


def test_unknown_scope_is_an_error_not_silently_ignored(engine: Engine, tmp_path: Path) -> None:
    with pytest.raises(UnknownScope):
        _refresh(engine, tmp_path / "w", tmp_path, groups={"123"})
    with pytest.raises(UnknownScope):
        _refresh(engine, tmp_path / "w", tmp_path, competitions={"456"})


def test_other_season_does_not_contaminate_existing_data(engine: Engine, tmp_path: Path) -> None:
    work = tmp_path / "work"
    _put(work, _league())
    _refresh(engine, work, tmp_path)
    nxt = _league()
    nxt["season"], nxt["competition"], nxt["group"] = {"external_id": "23"}, {"external_id": "30000001", "name": "C"}, {"external_id": "30000002"}
    for m in nxt["matches"]:
        m["external_match_id"] = "7" + m["external_match_id"]
    inv = {**_inventory([("30000001", "30000002")]), "season": {"external_id": "23", "name": "2027-28"}}
    _put(tmp_path / "work23", nxt, "30000001", "30000002")
    _refresh(engine, tmp_path / "work23", tmp_path, inv=inv)
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Season)) == 2 and s.scalar(select(func.count()).select_from(Competition)) == 2
        old = s.scalars(select(CompetitionGroup).where(CompetitionGroup.external_id == GROUP)).one()
        assert s.scalar(select(func.count()).select_from(Match).where(Match.group_id == old.id)) == 14


def test_identity_conflict_at_refresh_level_marks_the_run_partial(engine: Engine, tmp_path: Path) -> None:
    work = tmp_path / "work"
    other = "26738244"
    inv = _inventory([(COMP, GROUP), (COMP, other)])
    _put(work, _league())
    clash = _other_group(other, "")  # mismos IDs de partido que el grupo A
    _put(work, clash, grp=other)
    out = _refresh(engine, work, tmp_path, inv=inv)
    b = out["groups"][1]["datasets"]["matches"]
    assert b["status"] == "partial" and b["issue_codes"]["identity_conflict"] == 14 and out["status"] == "partial"
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Match).where(
            Match.group_id == select(CompetitionGroup.id).where(CompetitionGroup.external_id == GROUP).scalar_subquery())) == 14


# ------------------------------------------------------------------------------- dry-run y fallos de persistencia
def test_dry_run_reports_changes_without_writing(engine: Engine, tmp_path: Path) -> None:
    work = tmp_path / "work"
    _put(work, _league())
    out = _refresh(engine, work, tmp_path, dry_run=True)
    assert out["run_id"] is None and out["groups"][0]["datasets"]["matches"]["counts"]["inserted"] > 0
    assert _count(engine, Match) == 0 and _count(engine, IngestionRun) == 0 and _count(engine, DatasetRefresh) == 0


def test_failure_while_inserting_matches_rolls_back_the_whole_group(engine: Engine, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from futsal.repositories import league as repo

    work = tmp_path / "work"
    _put(work, _league())
    real, calls = repo._observe, {"n": 0}

    def boom(*a: Any, **k: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 5:
            raise RuntimeError("fallo simulado al insertar")
        real(*a, **k)

    monkeypatch.setattr(repo, "_observe", boom)
    out = _refresh(engine, work, tmp_path)
    m = out["groups"][0]["datasets"]["matches"]
    assert m["status"] == "failed" and out["status"] == "failed" and "fallo simulado" in m["message"]
    assert _count(engine, Match) == 0 and _count(engine, Team) == 0  # nada a medias
    with Session(engine) as s:
        child = s.get_one(IngestionRun, m["child_run_id"])
        assert child.status == "failed" and "fallo simulado" in (child.error_summary or "")
        assert s.get_one(IngestionRun, out["run_id"]).status == "failed"
    assert _count(engine, DatasetRefresh) == 0  # el grupo nunca llegó a existir: el intento fallido queda solo en la ejecución


def test_failure_while_updating_results_leaves_previous_data_intact(engine: Engine, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    from futsal.repositories import league as repo

    work = tmp_path / "work"
    _put(work, _league())
    _refresh(engine, work, tmp_path)
    _put(work, _league(**{"5575696": {"status": "finished", "home_score": 2, "away_score": 1},
                          "5575697": {"home_score": 9, "away_score": 9}}))
    real, calls = repo._observe, {"n": 0}

    def boom(*a: Any, **k: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("fallo simulado al actualizar")
        real(*a, **k)

    monkeypatch.setattr(repo, "_observe", boom)
    out = _refresh(engine, work, tmp_path)
    assert out["status"] == "failed"
    with Session(engine) as s:
        a = s.scalars(select(Match).where(Match.external_id == "5575696")).one()
        b = s.scalars(select(Match).where(Match.external_id == "5575697")).one()
        assert (a.status, b.home_score) == ("scheduled", 3)  # ni el primero ni el segundo se aplicaron


def test_unexpected_error_in_a_dataset_never_closes_the_run_as_successful(engine: Engine, tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from futsal.refresh import service

    work = tmp_path / "work"
    _put(work, _league())
    monkeypatch.setattr(service, "_stored_counts", lambda *a, **k: (_ for _ in ()).throw(OSError("disco")))
    out = _refresh(engine, work, tmp_path)
    assert out["status"] == "failed" and out["groups"][0]["datasets"]["matches"]["error_kind"] == "unexpected"
    assert _run(engine, out["run_id"]).status == "failed"


# ----------------------------------------------------------------------------------------------- tablas oficiales
TCOMP, TGROUP = "90000001", "90000002"


def _tables_world(engine: Engine) -> dict[str, Any]:
    with Session(engine) as s, s.begin():
        season = Season(external_id="99", name="2099")
        s.add(season)
        s.flush()
        comp = Competition(external_id=TCOMP, season_id=season.id, name="LIGA")
        s.add(comp)
        s.flush()
        group = CompetitionGroup(external_id=TGROUP, competition_id=comp.id, name="Grupo 1")
        s.add(group)
        s.flush()
        s.add_all(Team(external_id=f"9100000{i}", group_id=group.id, name=f"EQUIPO {i}") for i in range(1, 7))
    return {"season": {"external_id": "99", "name": "2099"}, "game_type": "3", "entries": [{
        "identification": "confirmed", "groups_status": "ok", "competition": {"external_id": TCOMP, "name": "LIGA"},
        "groups": [{"external_id": TGROUP, "name": "Grupo 1", "expected_rounds": 10, "expected_teams": 6}]}]}


def _tables(engine: Engine, tmp_path: Path, inv: dict[str, Any], site: FakeSite, **kw: Any) -> dict[str, Any]:
    opts = RefreshOptions(datasets=("standings", "scorers"), mode=kw.pop("mode", "incremental"), table_max_age_hours=0.0)
    return run_refresh(engine, inv, tmp_path / "w", tmp_path / "t", tmp_path / "r", site, opts, now=lambda: NOW, **kw)


def test_official_tables_are_fetched_not_rebuilt_and_a_second_run_changes_nothing(engine: Engine, tmp_path: Path) -> None:
    inv = _tables_world(engine)
    site = FakeSite({("clasificaciones", TGROUP, None): load("standings_round4_full.html"),
                     ("goleadores", TGROUP, None): load("scorers_full.html")})
    first = _tables(engine, tmp_path, inv, site)
    assert first["status"] == "success"
    again = _tables(engine, tmp_path, inv, site, )
    assert again["status"] == "no_changes"
    assert _count(engine, StandingsSnapshot) == 1 and _count(engine, ScorerSnapshot) == 1
    with Session(engine) as s:
        gid = int(s.scalar(select(CompetitionGroup.id)) or 0)
        f = group_freshness(s, gid, now=NOW + timedelta(hours=1))
    assert f is not None and f["datasets"]["standings"]["status"] == "fresh" and f["datasets"]["scorers"]["status"] == "fresh"


def test_failed_or_incomplete_table_keeps_the_last_valid_capture(engine: Engine, tmp_path: Path) -> None:
    inv = _tables_world(engine)
    good = FakeSite({("clasificaciones", TGROUP, None): load("standings_round4_full.html"),
                     ("goleadores", TGROUP, None): load("scorers_full.html")})
    _tables(engine, tmp_path, inv, good)
    broken = FakeSite({("clasificaciones", TGROUP, None): FetchError("HTTP 503"), ("goleadores", TGROUP, None): load("scorers_null.html")})
    out = _tables(engine, tmp_path, inv, broken, mode="full")
    assert out["status"] == "failed"
    assert _count(engine, StandingsSnapshot) == 1 and _count(engine, ScorerSnapshot) == 1
    from futsal.repositories import table_queries as tq

    with Session(engine) as s:
        gid = int(s.scalar(select(CompetitionGroup.id)) or 0)
        assert tq.standings(s, gid)["status"] == "ok"  # type: ignore[index]
        assert tq.scorer_table(s, gid)["status"] == "ok"  # type: ignore[index]
        fresh = group_freshness(s, gid, now=NOW + timedelta(hours=1))
    assert fresh is not None and any("falló" in w or "failed" in w for w in fresh["warnings"])


# ---------------------------------------------------------------------------------------------------- actas
HTML = (FIXTURES / "rffm_match_report.html").read_text(encoding="utf-8")


def _reports_world(engine: Engine, tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    work = tmp_path / "work"
    _put(work, _league())
    _refresh(engine, work, tmp_path)
    return work, _inventory()


def _reports(engine: Engine, work: Path, tmp_path: Path, fetch: Any, mode: str = "incremental") -> dict[str, Any]:
    return run_refresh(engine, _inventory(), work, tmp_path / "t", tmp_path / "reports", fetch,
                       RefreshOptions(datasets=("reports",), mode=mode), now=lambda: NOW)


def test_report_incorporated_once_and_unpublished_reports_are_not_errors(engine: Engine, tmp_path: Path) -> None:
    work, _ = _reports_world(engine, tmp_path)

    def fetch(url: str) -> str:
        if "/5575697?" in url:
            return HTML
        raise FetchError("HTTP 404")  # actas aún no publicadas

    out = _reports(engine, work, tmp_path, fetch)
    r = out["groups"][0]["datasets"]["reports"]
    assert r["status"] == "success" and r["counts"]["imported"] == 1 and r["counts"]["unavailable"] >= 1 and out["status"] == "success"
    events_after_first = _count(engine, MatchReportObservation)
    again = _reports(engine, work, tmp_path, fetch, mode="full")  # revisión completa: mismo acta
    assert again["groups"][0]["datasets"]["reports"]["status"] in ("no_changes", "success")
    assert _count(engine, MatchReportObservation) == events_after_first  # sin duplicar eventos ni observaciones
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM match_events")) == c.scalar(text("SELECT count(*) FROM match_events WHERE observation_id IN "
                                                                                      "(SELECT id FROM match_report_observations)"))


def _without_players(html: str) -> str:
    import re

    m = re.search(r'(<script id="__NEXT_DATA__"[^>]*>)(.*?)(</script>)', html, re.S)
    assert m is not None
    data = json.loads(m.group(2))
    game = data["props"]["pageProps"]["game"]
    for key in ("jugadores_equipo_local", "jugadores_equipo_visitante"):
        assert game[key]
        game[key] = []
    return html[:m.start(2)] + json.dumps(data) + html[m.end(2):]


def test_degraded_report_version_is_rejected_and_the_valid_one_is_kept(engine: Engine, tmp_path: Path) -> None:
    work, _ = _reports_world(engine, tmp_path)
    _reports(engine, work, tmp_path, lambda url: HTML if "/5575697?" in url else (_ for _ in ()).throw(FetchError("HTTP 404")))
    with engine.connect() as c:
        players_before = c.scalar(text("SELECT count(*) FROM match_players"))
        obs_before = c.scalar(text("SELECT count(*) FROM match_report_observations"))
    assert players_before
    blank = _without_players(HTML)
    out = _reports(engine, work, tmp_path, lambda url: blank if "/5575697?" in url else (_ for _ in ()).throw(FetchError("HTTP 404")),
                   mode="full")
    r = out["groups"][0]["datasets"]["reports"]
    assert r["status"] == "failed" and r["counts"]["rejected"] == 1 and "no trae jugadores" in r["message"] and out["status"] == "failed"
    with engine.connect() as c:  # la versión válida sigue siendo la vigente
        assert c.scalar(text("SELECT count(*) FROM match_players")) == players_before
        assert c.scalar(text("SELECT count(*) FROM match_report_observations")) == obs_before


# ------------------------------------------------------------------- fallos de persistencia de tablas y actas (11.4)
def _fail_after(monkeypatch: pytest.MonkeyPatch, module: Any, name: str, message: str) -> None:
    real = getattr(module, name)

    def boom(*a: Any, **k: Any) -> Any:
        real(*a, **k)  # el trabajo se hace en la transacción y después falla
        raise RuntimeError(message)

    monkeypatch.setattr(module, name, boom)


@pytest.mark.parametrize("kind,func_name,model", [("standings", "persist_standings", StandingsSnapshot),
                                                  ("scorers", "persist_scorers", ScorerSnapshot)])
def test_failure_replacing_official_tables_keeps_the_previous_capture(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                       kind: str, func_name: str, model: Any) -> None:
    from futsal.importer import table_service

    inv = _tables_world(engine)
    ok = FakeSite({("clasificaciones", TGROUP, None): load("standings_round4_full.html"), ("goleadores", TGROUP, None): load("scorers_full.html")})
    _tables(engine, tmp_path, inv, ok)
    assert _count(engine, model) == 1
    changed = load("standings_round4_full.html").replace('"puntos": "10"', '"puntos": "11"') if kind == "standings" \
        else load("scorers_full.html").replace('"goles": "6"', '"goles": "7"')
    assert changed != (load("standings_round4_full.html") if kind == "standings" else load("scorers_full.html"))
    _fail_after(monkeypatch, table_service, func_name, "fallo simulado al sustituir")
    site = FakeSite({("clasificaciones", TGROUP, None): changed if kind == "standings" else load("standings_round4_full.html"),
                     ("goleadores", TGROUP, None): changed if kind == "scorers" else load("scorers_full.html")})
    out = _tables(engine, tmp_path, inv, site, mode="full")
    assert out["groups"][0]["datasets"][kind]["status"] == "failed" and out["status"] in ("failed", "partial")
    assert _count(engine, model) == 1  # la captura nueva (snapshot + filas) se revirtió entera
    from futsal.repositories import table_queries as tq

    with Session(engine) as s:
        gid = int(s.scalar(select(CompetitionGroup.id)) or 0)
        served = tq.standings(s, gid) if kind == "standings" else tq.scorer_table(s, gid)
    assert served is not None and served["status"] == "ok"


def test_failure_while_importing_a_report_leaves_no_partial_report(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from futsal.repositories import match_reports

    work, _ = _reports_world(engine, tmp_path)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("fallo simulado tras insertar eventos")

    monkeypatch.setattr(match_reports, "events_score", boom)  # se invoca al final de import_report, con todo ya insertado
    out = _reports(engine, work, tmp_path, lambda url: HTML if "/5575697?" in url else (_ for _ in ()).throw(FetchError("HTTP 404")))
    assert out["groups"][0]["datasets"]["reports"]["status"] == "failed" and out["status"] == "failed"
    with engine.connect() as c:
        for table in ("match_reports", "match_report_observations", "match_events", "match_players"):
            assert c.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        assert c.scalar(text("SELECT status FROM ingestion_runs WHERE kind='report' ORDER BY id DESC LIMIT 1")) == "failed"
