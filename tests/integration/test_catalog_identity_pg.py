"""Migración 0007 (catálogo e identidad, revisión A2), importación de metadatos y `catalog-backfill` contra PostgreSQL."""

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from futsal import cli
from futsal.config.settings import database_url
from futsal.db.models import Competition, CompetitionGroup, Season, Team, TeamEntity
from futsal.importer.catalog_backfill import find_inventories, run_backfill
from futsal.importer.catalog_service import import_catalog

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "league_two_rounds.json"
NEW_TABLES = ("clubs", "team_entities", "competition_links", "catalog_overrides")
OFFICIAL = {"game_type_code": "3", "category_code": "238001", "category_name": "PRIMERA JUVENIL SALA",
            "category_group_code": "1", "category_group_name": "FUTBOL SALA", "format_code": "2", "active": "1",
            "start_date": "2026-10-19", "end_date": "2027-02-26", "ptos_ganado": "3", "minutos_juego": "20"}


def variant(tmp_path: Path, name: str, comp: str, group: str, salt: str, comp_name: str, *, share_teams: bool = False) -> Path:
    """Copia del fixture de liga con otra competición/grupo; con `share_teams` los equipos conservan su CodEquipo."""
    d: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    d["competition"].update(external_id=comp, name=comp_name)
    d["group"].update(external_id=group, name=f"Grupo {salt}")
    for m in d["matches"]:
        m["external_match_id"] = salt + m["external_match_id"]
        if not share_teams:
            m["home_team_external_id"] = salt + m["home_team_external_id"]
            m["away_team_external_id"] = salt + m["away_team_external_id"]
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    return path


def make_report(paths: dict[tuple[str, str], Path], meta: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    groups = []
    for (comp, grp), path in paths.items():
        g: dict[str, Any] = {"competition_id": comp, "group_id": grp, "status": "downloaded", "league_path": str(path)}
        if meta and comp in meta:
            g.update(competition_meta=meta[comp], expected_rounds=26, expected_teams=14, delegation="CENTRAL R.F.F.M.")
        groups.append(g)
    return {"groups": groups}


@pytest.fixture()
def paths(tmp_path: Path) -> dict[tuple[str, str], Path]:
    return {("900", "901"): variant(tmp_path, "a", "900", "901", "7", "COMP A", share_teams=True),
            ("900", "902"): variant(tmp_path, "b", "900", "902", "8", "COMP A", share_teams=True),
            ("910", "911"): variant(tmp_path, "c", "910", "911", "9", "COMP B")}


@pytest.fixture()
def cat_engine(engine: Engine) -> Iterator[Engine]:
    def clean() -> None:
        with engine.begin() as c:
            c.execute(text("TRUNCATE team_entities, clubs, competition_links, catalog_overrides, teams RESTART IDENTITY CASCADE"))
    clean()
    yield engine
    clean()


def competition(engine: Engine, external_id: str) -> Competition:
    with Session(engine) as s:
        c = s.query(Competition).filter_by(external_id=external_id).one()
        s.expunge(c)
        return c


def sql(engine: Engine, stmt: str, **params: Any) -> Any:
    with engine.begin() as c:
        result = c.execute(text(stmt), params)
        return result.all() if result.returns_rows else []


# --------------------------------------------------------------------------- migración


@pytest.fixture()
def lifecycle_db() -> Iterator[tuple[Engine, Config]]:
    """BD desechable propia: la migración se sube y se baja aquí sin tocar la BD compartida de la sesión."""
    admin_url = make_url(database_url())
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    name = f"futsal_test_{uuid.uuid4().hex[:10]}"
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = admin_url.set(database=name).render_as_string(hide_password=False)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    engine = create_engine(url)
    yield engine, cfg
    engine.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


def snapshot(engine: Engine, columns: dict[str, list[str]]) -> dict[str, tuple[int, str]]:
    """(recuento, suma de verificación md5) por tabla, solo sobre las columnas de la base (0006)."""
    out: dict[str, tuple[int, str]] = {}
    with engine.connect() as c:
        for table, cols in columns.items():
            row = "concat_ws('~', " + ", ".join(f"coalesce({col}::text, '<null>')" for col in cols) + ")"
            n, digest = c.execute(text(
                f"SELECT count(*), md5(coalesce(string_agg({row}, '|' ORDER BY {row}), '')) FROM {table}")).one()
            out[table] = (n, digest)
    return out


def test_migration_roundtrip_keeps_previous_data_identical(lifecycle_db: tuple[Engine, Config], paths: dict[tuple[str, str], Path]) -> None:
    engine, cfg = lifecycle_db
    command.upgrade(cfg, "head")
    assert all(r["import_status"] == "success" for r in import_catalog(engine, make_report(paths)))
    command.downgrade(cfg, "0006")  # BD real en 0006 con datos de tipo Fase 14 cargados por el importador
    insp = inspect(engine)
    assert not set(NEW_TABLES) & set(insp.get_table_names()) and "team_lineage" not in insp.get_view_names()
    columns = {t: [c["name"] for c in insp.get_columns(t)] for t in insp.get_table_names() if t != "alembic_version"}
    before = snapshot(engine, columns)
    assert before["matches"][0] > 0 and before["teams"][0] > 0 and before["competitions"][0] == 2

    command.upgrade(cfg, "0007")
    assert snapshot(engine, columns) == before
    with Session(engine) as s:  # los valores nuevos son los seguros por defecto
        comps = s.query(Competition).all()
        assert {(c.modality, c.scope, c.category, c.gender, c.kind, c.phase, c.division, c.review_status) for c in comps} == {
            ("unknown",) * 7 + ("needs_review",)}
        assert all(c.classification is None and c.active is None for c in comps)
        assert all(not x.is_current for x in s.query(Season).all())
    assert [sql(engine, f"SELECT count(*) FROM {t}")[0][0] for t in NEW_TABLES] == [0, 0, 0, 0]
    assert sql(engine, "SELECT count(*) FROM team_lineage")[0][0] == before["teams"][0]  # vista consultable: una fila por equipo

    # El backfill sí actualiza `updated_at` de las filas que enlaza/clasifica; el resto de lo previo debe quedar intacto al bajar.
    stable = {t: [c for c in cols if c != "updated_at"] for t, cols in columns.items()}
    before_stable = snapshot(engine, stable)
    run_backfill(engine, inventories=[])
    assert sql(engine, "SELECT count(*) FROM team_entities")[0][0] > 0
    command.downgrade(cfg, "0006")
    assert snapshot(engine, stable) == before_stable
    insp = inspect(engine)
    assert not set(NEW_TABLES) & set(insp.get_table_names()) and "team_lineage" not in insp.get_view_names()
    assert "team_entity_id" not in {c["name"] for c in insp.get_columns("teams")}
    assert not {"modality", "review_status", "classification"} & {c["name"] for c in insp.get_columns("competitions")}
    assert "uq_seasons_source_current" not in {i["name"] for i in insp.get_indexes("seasons")}

    command.upgrade(cfg, "head")
    assert snapshot(engine, stable) == before_stable


def test_new_indexes_and_view_columns_exist(engine: Engine) -> None:
    insp = inspect(engine)
    comp_idx = {i["name"]: i["column_names"] for i in insp.get_indexes("competitions")}
    assert comp_idx["ix_competitions_season_id_modality_category_gender"] == ["season_id", "modality", "category", "gender"]
    assert comp_idx["ix_competitions_review_status"] == ["review_status"]
    assert comp_idx["ix_competitions_source_category_code"] == ["source", "category_code"]
    assert "ix_teams_team_entity_id" in {i["name"] for i in insp.get_indexes("teams")}
    cols = {c["name"] for c in insp.get_columns("team_lineage")}
    assert {"team_id", "team_entity_id", "club_id", "season_id", "competition_id", "category", "gender", "group_id"} <= cols


# --------------------------------------------------------------------------- FK y CHECK


@pytest.fixture()
def seeded(cat_engine: Engine, paths: dict[tuple[str, str], Path]) -> Engine:
    import_catalog(cat_engine, make_report(paths))
    return cat_engine


def test_checks_reject_invalid_enumerations(seeded: Engine) -> None:
    sql(seeded, "INSERT INTO team_entities(source, external_code) VALUES ('rffm','T1')")
    for stmt in (
        "UPDATE competitions SET review_status='maybe' WHERE external_id='900'",
        "UPDATE team_entities SET club_link_status='guess'",
        "INSERT INTO catalog_overrides(source, kind, target_external_id, fields) VALUES ('rffm','club_merge','1','{}')",
    ):
        with pytest.raises(IntegrityError):
            sql(seeded, stmt)
    ids = [r[0] for r in sql(seeded, "SELECT id FROM competitions ORDER BY id")]
    for status, basis in (("weird", "category_code+name"), ("proposed", "magic")):
        with pytest.raises(IntegrityError):
            sql(seeded, "INSERT INTO competition_links(from_competition_id, to_competition_id, basis, confidence, status) "
                        "VALUES (:a, :b, :basis, 0.5, :st)", a=ids[0], b=ids[1], basis=basis, st=status)
    sql(seeded, "INSERT INTO competition_links(from_competition_id, to_competition_id, basis, confidence) VALUES (:a, :b, 'override', 1)",
        a=ids[0], b=ids[1])
    assert sql(seeded, "SELECT status, confidence FROM competition_links")[0][0] == "proposed"
    for status in ("confirmed", "rejected"):
        sql(seeded, "UPDATE competition_links SET status=:s", s=status)
    sql(seeded, "UPDATE team_entities SET club_link_status='official'")
    sql(seeded, "UPDATE competitions SET review_status='confirmed' WHERE external_id='900'")


def test_foreign_keys_and_uniqueness(seeded: Engine) -> None:
    with pytest.raises(IntegrityError):
        sql(seeded, "UPDATE teams SET team_entity_id=999999")
    with pytest.raises(IntegrityError):
        sql(seeded, "INSERT INTO team_entities(source, external_code, club_id) VALUES ('rffm','T9', 999999)")
    with pytest.raises(IntegrityError):
        sql(seeded, "INSERT INTO competition_links(from_competition_id, to_competition_id, basis, confidence) "
                    "VALUES (999998, 999999, 'override', 1)")
    sql(seeded, "INSERT INTO clubs(source, external_code, name) VALUES ('rffm','C1','CLUB')")
    with pytest.raises(IntegrityError):
        sql(seeded, "INSERT INTO clubs(source, external_code) VALUES ('rffm','C1')")
    sql(seeded, "INSERT INTO clubs(source, external_code) VALUES ('otra','C1')")
    sql(seeded, "INSERT INTO team_entities(source, external_code) VALUES ('rffm','T1')")
    with pytest.raises(IntegrityError):
        sql(seeded, "INSERT INTO team_entities(source, external_code) VALUES ('rffm','T1')")
    sql(seeded, "UPDATE team_entities SET club_id=(SELECT id FROM clubs WHERE external_code='C1' AND source='rffm')")
    ids = [r[0] for r in sql(seeded, "SELECT id FROM competitions ORDER BY id")]
    sql(seeded, "INSERT INTO competition_links(from_competition_id, to_competition_id, basis, confidence) VALUES (:a,:b,'override',0.9)",
        a=ids[0], b=ids[1])
    with pytest.raises(IntegrityError):
        sql(seeded, "INSERT INTO competition_links(from_competition_id, to_competition_id, basis, confidence) "
                    "VALUES (:a,:b,'override',0.9)", a=ids[0], b=ids[1])
    # una sola temporada actual por fuente
    sql(seeded, "UPDATE seasons SET is_current=true")
    sql(seeded, "INSERT INTO seasons(source, external_id, is_current) VALUES ('rffm','21', false), ('rffm','20', false), ('otra','22', true)")
    with pytest.raises(IntegrityError):
        sql(seeded, "UPDATE seasons SET is_current=true WHERE external_id='21'")


# --------------------------------------------------------------------------- importación de metadatos


def test_import_catalog_persists_official_fields_and_classification(seeded: Engine, paths: dict[tuple[str, str], Path]) -> None:
    results = import_catalog(seeded, make_report(paths, {"900": OFFICIAL}))
    by_comp = {r["competition_id"]: r for r in results}
    assert "catalog_meta_changes" in by_comp["900"] and "catalog_meta_changes" not in by_comp["910"]
    c = competition(seeded, "900")
    assert (c.game_type_code, c.category_code, c.category_name, c.category_group_name, c.format_code, c.active) == (
        "3", "238001", "PRIMERA JUVENIL SALA", "FUTBOL SALA", "2", True)
    assert str(c.start_date) == "2026-10-19" and c.source_meta == {"ptos_ganado": "3", "minutos_juego": "20"}
    assert (c.modality, c.scope, c.category, c.gender, c.kind, c.phase, c.division, c.review_status) == (
        "futsal", "federated", "juvenil", "male", "league", "regular", "primera", "auto")
    assert c.classification is not None and c.classification["gender"]["rule_id"] == "implicit_male"
    with Session(seeded) as s:
        g = s.query(CompetitionGroup).filter_by(external_id="901").one()
        assert (g.expected_rounds, g.expected_teams, g.delegation) == (26, 14, "CENTRAL R.F.F.M.")
    assert competition(seeded, "910").review_status == "needs_review"  # sin informe de metadatos: no se toca
    again = import_catalog(seeded, make_report(paths, {"900": OFFICIAL}))
    assert not any("catalog_meta_changes" in r for r in again)  # reimportar es idempotente


def test_import_respects_override_confirmed_and_dry_run(seeded: Engine, paths: dict[tuple[str, str], Path]) -> None:
    import_catalog(seeded, make_report(paths, {"900": OFFICIAL}), dry_run=True)
    assert competition(seeded, "900").category_name is None  # dry-run no persiste metadatos
    sql(seeded, "INSERT INTO catalog_overrides(source, kind, target_external_id, fields, reason) "
                "VALUES ('rffm','competition','900','{\"category\":\"cadete\"}','revisado')")
    import_catalog(seeded, make_report(paths, {"900": OFFICIAL}))
    c = competition(seeded, "900")
    assert (c.category, c.classification["category"]["source"] if c.classification else None) == ("cadete", "override")
    assert c.gender == "male"  # el resto sigue las reglas
    sql(seeded, "UPDATE competitions SET review_status='confirmed', kind='cup' WHERE external_id='900'")
    before = competition(seeded, "900")
    import_catalog(seeded, make_report(paths, {"900": {**OFFICIAL, "category_name": "PRIMERA INFANTIL SALA"}}))
    after = competition(seeded, "900")
    assert (after.kind, after.review_status, after.category_name, after.classification) == (
        "cup", "confirmed", before.category_name, before.classification)


# --------------------------------------------------------------------------- backfill


def test_backfill_creates_one_entity_per_external_id_and_links_all_teams(seeded: Engine) -> None:
    with Session(seeded) as s:
        total, distinct = s.query(Team).count(), len({t.external_id for t in s.query(Team)})
    assert distinct < total  # los equipos compartidos entre grupos son la misma identidad
    r1 = run_backfill(seeded, inventories=[])
    assert (r1["entities_created"], r1["teams_linked"], r1["teams_unlinked"], r1["team_entities_total"]) == (distinct, total, 0, distinct)
    assert r1["changes"] >= distinct + total
    with Session(seeded) as s:
        assert all(t.team_entity_id is not None for t in s.query(Team))
        by_entity: dict[int, set[str]] = {}
        for t in s.query(Team):
            by_entity.setdefault(t.team_entity_id, set()).add(t.external_id)  # type: ignore[arg-type]
        assert all(len(v) == 1 for v in by_entity.values())
        e = s.query(TeamEntity).first()
        assert e is not None and e.club_id is None and e.club_link_status == "unknown" and e.name
    r2 = run_backfill(seeded, inventories=[])
    assert r2["changes"] == 0 and (r2["entities_created"], r2["teams_linked"], r2["competitions_changed"]) == (0, 0, 0)
    assert sql(seeded, "SELECT count(*) FROM team_lineage WHERE team_entity_id IS NULL")[0][0] == 0


def test_backfill_by_name_only_marks_needs_review_and_is_idempotent(seeded: Engine) -> None:
    sql(seeded, "UPDATE competitions SET name='PRIMERA JUVENIL FEMENINO FS' WHERE external_id='900'")
    run_backfill(seeded, inventories=[])
    c = competition(seeded, "900")
    assert (c.modality, c.scope, c.category, c.gender, c.kind, c.review_status) == (
        "futsal", "unknown", "juvenil", "female", "unknown", "needs_review")
    assert c.classification is not None and c.classification["modality"]["confidence"] == 0.6
    assert run_backfill(seeded, inventories=[])["changes"] == 0
    other = competition(seeded, "910")  # «COMP B»: sin señal alguna
    assert (other.modality, other.category, other.gender, other.review_status) == ("unknown", "unknown", "unknown", "needs_review")


def write_inventory(tmp_path: Path) -> Path:
    inv = {"season": {"external_id": "22", "name": "2026-2027", "start_date": "2026-07-01", "end_date": "2027-09-01"},
           "entries": [{"competition": {"external_id": "900", "name": "COMP A", "game_type": "3", "category": "PRIMERA JUVENIL SALA",
                                        "category_id": "238001", "active": "1", "start_date": "2026-10-19", "end_date": "2027-02-26",
                                        "category_group_name": "FUTBOL SALA", "format_code": "2"},
                        "groups": [{"external_id": "901", "name": "Grupo 7", "expected_rounds": 26, "expected_teams": 14,
                                    "delegation": "CENTRAL R.F.F.M."}]}]}
    path = tmp_path / "s22_t3" / "inventory.json"
    path.parent.mkdir()
    path.write_text(json.dumps(inv), encoding="utf-8")
    return path


def test_backfill_from_inventory_fills_competitions_groups_and_seasons(seeded: Engine, tmp_path: Path) -> None:
    inv = write_inventory(tmp_path)
    assert find_inventories(inv, tmp_path) == [inv] and find_inventories(None, tmp_path) == [inv]
    assert find_inventories(None, tmp_path / "no-existe") == []
    dry = run_backfill(seeded, inventories=[inv], dry_run=True)
    assert dry["dry_run"] and dry["changes"] > 0 and competition(seeded, "900").category_name is None
    assert sql(seeded, "SELECT count(*) FROM team_entities")[0][0] == 0  # nada persistido
    r1 = run_backfill(seeded, inventories=[inv])
    assert r1["competitions_changed"] == 2 and r1["groups_changed"] == 1 and r1["seasons_changed"] == 1
    c = competition(seeded, "900")
    assert (c.category, c.gender, c.kind, c.review_status, c.category_name) == ("juvenil", "male", "league", "auto", "PRIMERA JUVENIL SALA")
    with Session(seeded) as s:
        season = s.query(Season).one()
        assert (season.label, str(season.start_date), str(season.end_date), season.is_current) == ("2026-2027", "2026-07-01", "2027-09-01", False)
    assert run_backfill(seeded, inventories=[inv])["changes"] == 0


def test_backfill_keeps_override_and_confirmed_and_reclassifies_on_request(seeded: Engine, tmp_path: Path) -> None:
    inv = write_inventory(tmp_path)
    run_backfill(seeded, inventories=[inv])
    override = {"value": "cadete", "source": "override", "rule_id": "override", "confidence": 1.0}
    with Session(seeded) as s:
        a = s.query(Competition).filter_by(external_id="900").one()
        a.category = "cadete"
        a.classification = {**(a.classification or {}), "category": override}
        b = s.query(Competition).filter_by(external_id="910").one()
        b.review_status, b.category, b.gender = "confirmed", "juvenil", "female"
        b.classification = {"category": {"value": "juvenil", "source": "override", "rule_id": "override", "confidence": 1.0},
                            "gender": {"value": "female", "source": "rule", "rule_id": "x", "confidence": 0.8}}
        s.commit()
    frozen = (competition(seeded, "910").classification, competition(seeded, "910").gender)
    r = run_backfill(seeded, inventories=[inv])
    assert r["changes"] == 0 and r["competitions_skipped_confirmed"] == 1 and r["competitions_with_override"] == 2
    a, b = competition(seeded, "900"), competition(seeded, "910")
    assert a.category == "cadete" and a.classification and a.classification["category"] == override
    assert (b.review_status, b.classification, b.gender) == ("confirmed", frozen[0], frozen[1])

    r2 = run_backfill(seeded, inventories=[inv], reclassify_confirmed=True)
    assert r2["competitions_changed"] == 1 and r2["competitions_skipped_confirmed"] == 0
    b2 = competition(seeded, "910")
    assert b2.classification is not None
    assert b2.gender == "unknown" and b2.classification["gender"]["previous"] == "female"  # valor anterior guardado
    assert b2.category == "juvenil" and b2.classification["category"]["source"] == "override"  # un override nunca se toca
    assert b2.review_status == "needs_review"
    assert competition(seeded, "900").category == "cadete"
    assert run_backfill(seeded, inventories=[inv], reclassify_confirmed=True)["changes"] == 0  # y sigue siendo idempotente
    assert competition(seeded, "910").classification == b2.classification


def test_cli_catalog_backfill(seeded: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    monkeypatch.setattr("futsal.db.session.make_engine", lambda url=None: seeded)
    inv = write_inventory(tmp_path)
    assert cli.main(["catalog-backfill", "--dry-run", "--inventory", str(inv)]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert cli.main(["catalog-backfill", "--inventory", str(inv), "--reclassify-confirmed"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["changes"] > 0 and first["dry_run"] is False
    assert cli.main(["catalog-backfill", "--inventory", str(inv)]) == 0
    assert json.loads(capsys.readouterr().out)["changes"] == 0
