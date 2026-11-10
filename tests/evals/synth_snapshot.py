"""Snapshot SINTÉTICO de desarrollo (solo para pruebas; NUNCA es la referencia: su manifiesto lleva `synthetic: true`).

Parte de las fixtures del repo (`league_two_rounds.json`, el acta `rffm_match_report.html`) y del importador existente; el resto se
inserta con SQL/ORM. Resultado: 2 temporadas, 3 competiciones (senior, juvenil, femenina), 4 grupos, homónimos entre grupos
(«C.D. DOSA» en senior, juvenil y femenina de otra temporada; «TORRES» y «CORAZONISTAS 'A'» en dos grupos senior), un club con equipos A y B,
grupos sin clasificación oficial, partidos terminados sin acta y un acta almacenada.

Ampliación multiámbito (estrato S10): goleadores oficiales también en el juvenil y el femenino (rankings por categoría), actas
sin eventos de otros grupos con árbitros (uno con partidos en dos competiciones, otro con un solo partido), un jugador de un
solo equipo («QUINTANAR PEREZ, PABLO») y un homónimo de jugador en dos equipos («GARCIA CASADO, DAVID»).
"""

import copy
import json
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from futsal.db.models import Player
from futsal.db.table_models import ScorerRow, ScorerSnapshot, StandingsRow, StandingsSnapshot
from futsal.importer.report_service import run_report_import
from futsal.importer.service import run_import
from futsal.ingestion.rffm.report_parser import parse_match_report

FIX = Path(__file__).parents[1] / "fixtures"
REPORT_URL = "https://www.rffm.es/acta-partido/5575697?temporada=22&competicion=26738243&grupo=26738245"
AS_OF = "2026-10-20T12:00:00"
SYNTHETIC_ID = "synthetic-dev"

SENIOR = ("26738243", "PRIMERA DIVISION AUTONOMICA AFICIONADO FS")
JUVENIL = ("26738300", "PREFERENTE JUVENIL FS")
FEMENINA = ("26738400", "LIGA FEMENINA FS")
SCORERS = ("JUAN PEREZ GARCIA", "PABLO MARTIN LOPEZ", "ALVARO RUIZ SANZ", "DAVID GOMEZ NIETO", "SERGIO LOPEZ VEGA",
           "ADRIAN SANZ CRUZ", "MARIO NIETO DIAZ", "IVAN CRUZ MORA")


def _league(season: tuple[str, str], comp: tuple[str, str], group: tuple[str, str], rename: Any, match_offset: int,
            extra_round: bool = False) -> dict[str, Any]:
    data = copy.deepcopy(json.loads((FIX / "league_two_rounds.json").read_text(encoding="utf-8")))
    data["season"] = {"external_id": season[0], "name": season[1]}
    data["competition"] = {"external_id": comp[0], "name": comp[1]}
    data["group"] = {"external_id": group[0], "name": group[1]}
    for m in data["matches"]:
        m["home_team_name"], m["away_team_name"] = rename(m["home_team_name"]), rename(m["away_team_name"])
        m["external_match_id"] = str(int(m["external_match_id"]) + match_offset)
    for r in data["rounds"]:
        r["source_url"] = r["source_url"].replace("26738245", group[0])
    if extra_round:  # jornada 3 programada tras el as_of (para «este fin de semana» y «próximo partido»)
        teams: dict[str, str] = {}
        for m in data["matches"]:
            teams[m["home_team_external_id"]], teams[m["away_team_external_id"]] = m["home_team_name"], m["away_team_name"]
        ids = sorted(teams)
        base = data["matches"][0]
        data["rounds"].append({**data["rounds"][-1], "external_round_id": "3", "visible_round_label": "3", "visible_round_number": 3,
                               "scheduled_date": "2026-10-24", "matches": 7})
        for i in range(0, 14, 2):
            data["matches"].append({
                **base, "external_match_id": str(match_offset + 900 + i), "external_round_id": "3", "status": "scheduled",
                "status_raw": "estado=0;situacion_juego=;motivo_estado=", "home_score": None, "away_score": None,
                "home_team_external_id": ids[i], "home_team_name": teams[ids[i]], "away_team_external_id": ids[i + 1],
                "away_team_name": teams[ids[i + 1]], "scheduled_date": "2026-10-24" if i < 8 else "2026-10-25",
                "scheduled_time": "18:30:00", "venue": f"PABELLON {i // 2 + 1}"})
    return data


def _keep(names: set[str], suffix: str, rewrite: dict[str, str] | None = None) -> Any:
    def rename(name: str) -> str:
        if rewrite and name in rewrite:
            return rewrite[name]
        return name if name in names else f"{name.replace(chr(39), '')} {suffix}"
    return rename


def _standings(session: Session, group_ext: str, round_number: int = 2) -> None:
    rows = session.execute(text(
        "SELECT t.id, t.external_id, t.name, m.home_team_id, m.away_team_id, m.home_score, m.away_score "
        "FROM teams t JOIN competition_groups g ON g.id = t.group_id "
        "LEFT JOIN matches m ON m.group_id = g.id AND m.status = 'finished' AND t.id IN (m.home_team_id, m.away_team_id) "
        "WHERE g.external_id = :g ORDER BY t.id, m.id"), {"g": group_ext}).all()
    table: dict[int, dict[str, Any]] = {}
    for tid, ext, name, home, away, hs, aws in rows:
        e = table.setdefault(tid, {"ext": ext, "name": name, "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0})
        if hs is None:
            continue
        gf, ga = (hs, aws) if tid == home else (aws, hs)
        e["p"] += 1
        e["gf"] += gf
        e["ga"] += ga
        e["w" if gf > ga else "l" if gf < ga else "d"] += 1
    order = sorted(table.items(), key=lambda kv: (-(3 * kv[1]["w"] + kv[1]["d"]), -(kv[1]["gf"] - kv[1]["ga"]), -kv[1]["gf"], kv[1]["name"]))
    gid = session.execute(text("SELECT id FROM competition_groups WHERE external_id = :g"), {"g": group_ext}).scalar_one()
    snap = StandingsSnapshot(group_id=gid, round_number=round_number, current_round=round_number, status="ok",
                             content_hash=f"synthetic-{group_ext}", row_count=len(order), expected_teams=len(order),
                             source_url="synthetic://standings", fetched_at=_as_of())
    session.add(snap)
    session.flush()
    for pos, (tid, e) in enumerate(order, 1):
        session.add(StandingsRow(snapshot_id=snap.id, position=pos, team_external_id=e["ext"], team_id=tid, team_name=e["name"],
                                 played=e["p"], won=e["w"], drawn=e["d"], lost=e["l"], goals_for=e["gf"], goals_against=e["ga"],
                                 points=3 * e["w"] + e["d"]))


def _as_of() -> Any:
    from futsal.evals.snapshot import parse_as_of
    return parse_as_of(AS_OF)


def _scorers(session: Session, group_ext: str, names: tuple[str, ...] = SCORERS, prefix: str = "S", top: int | None = None) -> None:
    gid = session.execute(text("SELECT id FROM competition_groups WHERE external_id = :g"), {"g": group_ext}).scalar_one()
    teams = session.execute(text("SELECT id, external_id, name FROM teams WHERE group_id = :g ORDER BY id"), {"g": gid}).all()
    top = len(names) + 1 if top is None else top
    snap = ScorerSnapshot(group_id=gid, status="ok", content_hash=f"synthetic-sc-{group_ext}", row_count=len(names),
                          goals_total=sum(top - i for i in range(len(names))), source_url="synthetic://scorers", fetched_at=_as_of())
    session.add(snap)
    session.flush()
    for i, name in enumerate(names):
        tid, ext, tname = teams[i % len(teams)]
        player = Player(source="rffm", external_id=f"{prefix}{i}", display_name=name)
        session.add(player)
        session.flush()
        session.add(ScorerRow(snapshot_id=snap.id, row_order=i + 1, player_external_id=f"{prefix}{i}", player_id=player.id, player_name=name,
                              team_external_id=ext, team_id=tid, team_name=tname, matches_played=2, goals=top - i))


def _report(session: Session, group_ext: str, nth: int, ext: str, officials: list[tuple[str, str, str]]) -> None:
    """Acta sin eventos (copia de la observación del acta real) para el `nth` partido terminado del grupo, con sus oficiales."""
    match_id = session.execute(text(
        "SELECT m.id FROM matches m JOIN competition_groups g ON g.id = m.group_id WHERE g.external_id = :g AND m.status = 'finished' "
        "AND NOT EXISTS (SELECT 1 FROM match_reports r WHERE r.match_id = m.id) ORDER BY m.id OFFSET :n LIMIT 1"),
        {"g": group_ext, "n": nth}).scalar_one()
    rid = session.execute(text("INSERT INTO match_reports (match_id, source, external_id) VALUES (:m, 'rffm', :e) RETURNING id"),
                          {"m": match_id, "e": ext}).scalar_one()
    oid = session.execute(text(
        "INSERT INTO match_report_observations (report_id, ingestion_run_id, observed_at, content_hash, source_url, closed, status, "
        "round_external_id) SELECT :r, ingestion_run_id, observed_at, :h, 'synthetic://report', true, 'finished', round_external_id "
        "FROM match_report_observations WHERE id = 1 RETURNING id"), {"r": rid, "h": f"synthetic-report-{ext}"}).scalar_one()
    for seq, (name, role, official_id) in enumerate(officials, 1):
        session.execute(text("INSERT INTO match_officials (observation_id, display_name, role, external_id, sequence) "
                             "VALUES (:o, :n, :r, :e, :s)"), {"o": oid, "n": name, "r": role, "e": official_id, "s": seq})


def _set_score(session: Session, group_ext: str, home: str, away: str, hs: int, aws: int) -> None:
    session.execute(text(
        "UPDATE matches SET home_score = :hs, away_score = :a WHERE id = (SELECT m.id FROM matches m "
        "JOIN teams h ON h.id = m.home_team_id JOIN teams t ON t.id = m.away_team_id JOIN competition_groups g ON g.id = m.group_id "
        "WHERE g.external_id = :g AND h.name = :h AND t.name = :t)"), {"hs": hs, "a": aws, "g": group_ext, "h": home, "t": away})


def _tweak_results(session: Session) -> None:
    """Rompe los empates entre grupos gemelos (mismas fixtures) para que haya un mejor equipo único por categoría y métrica."""
    z = "26738246"
    _set_score(session, z, "C.D.E. ALARO DEPORTIVA A ZONA 3", "ESC. DEP. DE FUTBOL DE TORRES", 13, 2)  # goles a favor únicos
    _set_score(session, z, "C.D. LOPE DE VEGA B ZONA 3", "AGRUPACION AUTO-ESCUELA SIMANCAS B ZONA 3", 1, 3)  # goles en contra únicos
    _set_score(session, z, "ESC. DEP. DE FUTBOL DE TORRES", "CLUB CORAZONISTAS 'A'", 7, 4)  # el homónimo de otro grupo tiene otro marcador
    _set_score(session, z, "A. D. OBISPO PERELLO B ZONA 3", "C.D. DOSA ZONA 3", 3, 3)  # victorias y racha únicas (el DOSA del grupo 2)


def _tweak_calendar(session: Session) -> None:
    """Un club «ALAMO» con equipos A y B y partidos el sábado 7 de noviembre (mañana y tarde) en dos grupos: para las preguntas literales."""
    for ext, old, new in (("26738245", "TORREJON SALA FIVE PLAY 'B'", "C.D. ALAMO 'A'"), ("26738246", "TORREJON SALA FIVE PLAY B ZONA 3", "C.D. ALAMO 'B'")):
        session.execute(text("UPDATE teams SET name = :n WHERE name = :o AND group_id = (SELECT id FROM competition_groups WHERE external_id = :g)"),
                        {"n": new, "o": old, "g": ext})
    for ext, hour in (("26738245", "18:30"), ("26738301", "11:00")):
        session.execute(text(
            "UPDATE matches SET scheduled_date = '2026-11-07', scheduled_time = :h WHERE status = 'scheduled' AND scheduled_date = '2026-12-05' "
            "AND group_id = (SELECT id FROM competition_groups WHERE external_id = :g)"), {"h": hour, "g": ext})


def _tweak_states(session: Session) -> None:
    """Un partido aplazado, uno suspendido, un acta sin cerrar y un equipo retirado (sin partidos ni clasificación)."""
    for ext, status in (("26738401", "postponed"), ("26738246", "suspended")):
        session.execute(text("UPDATE matches SET status = :s WHERE status = 'scheduled' AND scheduled_date = '2026-12-05' "
                             "AND group_id = (SELECT id FROM competition_groups WHERE external_id = :g)"), {"s": status, "g": ext})
    session.execute(text("INSERT INTO teams (group_id, source, external_id, name) SELECT id, 'rffm', 'SYN-RETIRADO', 'C.D. SALA RETIRADA' "
                         "FROM competition_groups WHERE external_id = '26738245'"))


def _people(session: Session) -> None:
    """Árbitros en varios grupos y jugadores con nombre «APELLIDOS, NOMBRE» (uno único y un homónimo en otro equipo)."""
    _report(session, "26738301", 0, "SYN-R1", [("GARCIA LASA, ALVARO", "ARBITRO", "5167652")])
    _report(session, "26738246", 0, "SYN-R2", [("GARCIA LASA, ALVARO", "ARBITRO", "5167652")])
    _report(session, "26738246", 1, "SYN-R3", [("BERMEJO SUAREZ, HUGO", "ARBITRO", "5009911")])
    _report(session, "26738401", 0, "SYN-R4", [("GARVALENA CRESPO, MARTA", "ARBITRO", "5001595")])
    session.execute(text("UPDATE match_report_observations SET closed = false WHERE content_hash = 'synthetic-report-SYN-R1'"))
    season = session.execute(text("SELECT c.season_id FROM competition_groups g JOIN competitions c ON c.id = g.competition_id "
                                  "WHERE g.external_id = '26738246'")).scalar_one()
    for ext, name, team in (("P1", "QUINTANAR PEREZ, PABLO", "CLUB CORAZONISTAS 'B'"), ("P2", "GARCIA CASADO, DAVID", "C.D. DOSA ZONA 3")):
        player = Player(source="rffm", external_id=ext, display_name=name)
        session.add(player)
        session.flush()
        team_id = session.execute(text("SELECT t.id FROM teams t JOIN competition_groups g ON g.id = t.group_id "
                                       "WHERE g.external_id = '26738246' AND t.name = :n"), {"n": team}).scalar_one()
        session.execute(text("INSERT INTO player_team_memberships (player_id, team_id, season_id) VALUES (:p, :t, :s)"),
                        {"p": player.id, "t": team_id, "s": season})


def _kb(session: Session) -> None:
    """Un documento indexado de reglamento (FIFA) con el fragmento de la duración de las mitades, para las pruebas de reglamento."""
    session.execute(text(
        "INSERT INTO kb_documents (id, doc_key, title, doc_type, language, structure_profile) "
        "VALUES (1, 'fifa-futsal-laws-2025-26', 'Reglas de Juego de Futsal 2025/26', 'reglamento', 'es', 'laws')"))
    session.execute(text(
        "INSERT INTO kb_document_versions (id, document_id, content_hash, file_path, file_bytes, page_count, extraction_status, "
        "index_status, is_current) VALUES (1, 1, 'synthetic', 'synthetic.pdf', 1, 70, 'ok', 'indexed', true)"))
    session.execute(text(
        "INSERT INTO kb_chunks (id, version_id, ordinal, rule_number, rule_title, page_start, page_end, text, char_count) "
        "VALUES (1, 1, 1, 7, 'Duración del partido', 60, 60, "
        "'Regla 7. Duración del partido. El partido se compone de dos mitades iguales de 20 minutos de tiempo efectivo.', 100)"))


def build_synthetic(engine: Engine, workdir: Path) -> dict[str, Any]:
    """Construye la BD sintética sobre `engine` (esquema ya migrado) y devuelve un resumen."""
    with engine.begin() as c:
        c.execute(text("TRUNCATE seasons, competitions, competition_groups, rounds, teams, matches, match_observations, "
                       "ingestion_runs, data_quality_issues, players RESTART IDENTITY CASCADE"))
    s26, s25 = ("22", "2026-27"), ("21", "2025-26")
    files = {
        "l1": _league(s26, SENIOR, ("26738245", "Grupo 2"), lambda n: n, 0),
        "l2": _league(s26, JUVENIL, ("26738301", "Grupo 1"), _keep({"C.D. DOSA", "C.D. LOPE DE VEGA 'B'"}, "JUV"), 100000),
        "l3": _league(s26, SENIOR, ("26738246", "Grupo 3"), _keep(
            {"ESC. DEP. DE FUTBOL DE TORRES", "CLUB CORAZONISTAS 'A'"}, "ZONA 3",
            {"COLEGIO CARDENAL SPINOLA CHAMARTIN 'B'": "CLUB CORAZONISTAS 'B'"}), 200000, extra_round=True),
        "l4": _league(s25, FEMENINA, ("26738401", "Grupo Unico"), _keep({"C.D. DOSA"}, "FEM"), 300000),
    }
    for name, data in files.items():
        path = workdir / f"{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        assert run_import(engine, path).status == "success"
    html = (FIX / "rffm_match_report.html").read_text(encoding="utf-8")
    report = workdir / "report.json"
    report.write_text(parse_match_report(html, REPORT_URL).model_dump_json(), encoding="utf-8")
    assert run_report_import(engine, report).status == "success"
    with Session(engine) as session:
        _tweak_results(session)
        _tweak_calendar(session)
        _tweak_states(session)
        _standings(session, "26738245")
        _standings(session, "26738246")
        _scorers(session, "26738245")
        _scorers(session, "26738301", ("RUIZ ORTEGA, LUCAS", "NAVARRO DIAZ, MARC", "FUENTES GIL, BRUNO"), "J", top=7)
        _scorers(session, "26738401", ("MORA PARDO, LAURA", "SANZ LEAL, IRENE", "VEGA PRIETO, NURIA"), "F", top=5)
        _people(session)
        _kb(session)
        session.commit()
    return {"groups": 4}
