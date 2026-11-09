"""Clasificador de competiciones del catálogo (revisión A2): catálogos reales, adversariales y prueba de oro del `classify` legado."""

import json
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from futsal.config.competition_aliases import load_aliases
from futsal.config.targets import CATALOG_SCOPE
from futsal.domain.catalog_classify import ATTRS, CompetitionMeta, classify_competition
from futsal.domain.competitions import Taxonomy, classify
from futsal.importer.catalog_service import load_source_config

FIXTURES = Path(__file__).parent / "fixtures"
CATALOGS = FIXTURES / "rffm_catalog"
CFG = load_source_config("rffm")
FIELDS = ("scope", "category", "gender", "kind", "phase", "division")


def _load(season: int) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((CATALOGS / f"api_competitions_s{season}_t3.json").read_text(encoding="utf-8"))
    return data


def meta_of(raw: dict[str, Any]) -> CompetitionMeta:
    return CompetitionMeta(raw["nombre"], raw["codigo_tipo_juego"], raw["CodigoCategoria"], raw["NombreCategoria"],
                           raw["cod_grupo_categoria"], raw["nombre_grupo_categoria"], raw["tipo_competicion"])


REAL = {(s, c["codigo"]): c for s in (22, 21, 10) for c in _load(s)}

# (temporada, código, scope, category, gender, kind, phase, division, review_status): revisado a mano contra el nombre y NombreCategoria.
TABLE: list[tuple[Any, ...]] = [
    (22, '26738241', 'other', 'universitario', 'female', 'tournament', 'unknown', 'unknown', 'auto'),
    (22, '26738238', 'other', 'universitario', 'male', 'tournament', 'first', 'unknown', 'auto'),
    (22, '26738296', 'federated', 'senior', 'male', 'tournament', 'unknown', 'segunda_b', 'auto'),
    (22, '26738221', 'federated', 'senior', 'male', 'league', 'regular', 'tercera', 'auto'),
    (22, '28403858', 'federated', 'senior', 'male', 'cup', 'final', 'primera_autonomica', 'auto'),
    (22, '28403847', 'federated', 'senior', 'male', 'cup', 'preliminary', 'primera_autonomica', 'auto'),
    (22, '26738243', 'federated', 'senior', 'male', 'league', 'regular', 'primera_autonomica', 'auto'),
    (22, '26738246', 'federated', 'senior', 'male', 'league', 'regular', 'preferente', 'auto'),
    (22, '28403930', 'federated', 'senior', 'male', 'cup', 'final', 'primera', 'auto'),
    (22, '28403871', 'federated', 'senior', 'male', 'cup', 'preliminary', 'primera', 'auto'),
    (22, '27776951', 'federated', 'senior', 'male', 'league', 'regular', 'primera', 'auto'),
    (22, '26843411', 'federated', 'juvenil', 'male', 'tournament', 'unknown', 'division_honor', 'auto'),
    (22, '26749998', 'federated', 'juvenil', 'male', 'league', 'regular', 'primera_autonomica', 'auto'),
    (22, '26738220', 'federated', 'juvenil', 'male', 'league', 'regular', 'preferente', 'auto'),
    (22, '28404058', 'federated', 'juvenil', 'male', 'cup', 'final', 'primera', 'auto'),
    (22, '28404003', 'federated', 'juvenil', 'male', 'cup', 'preliminary', 'primera', 'auto'),
    (22, '26738228', 'federated', 'juvenil', 'male', 'league', 'regular', 'primera', 'auto'),
    (22, '28414015', 'federated', 'cadete', 'male', 'cup', 'preliminary', 'primera_autonomica', 'auto'),
    (22, '26738292', 'federated', 'cadete', 'male', 'league', 'regular', 'primera_autonomica', 'auto'),
    (22, '26750003', 'federated', 'cadete', 'male', 'league', 'regular', 'preferente', 'auto'),
    (22, '28440556', 'federated', 'cadete', 'male', 'cup', 'final', 'primera', 'auto'),
    (22, '26750007', 'federated', 'cadete', 'male', 'league', 'regular', 'primera', 'auto'),
    (22, '26750011', 'federated', 'infantil', 'male', 'league', 'regular', 'primera_autonomica', 'auto'),
    (22, '26738249', 'federated', 'infantil', 'male', 'league', 'regular', 'preferente', 'auto'),
    (22, '28441161', 'federated', 'infantil', 'male', 'cup', 'final', 'primera', 'auto'),
    (22, '28415103', 'federated', 'infantil', 'male', 'cup', 'preliminary', 'primera', 'auto'),
    (22, '26750015', 'federated', 'infantil', 'male', 'league', 'regular', 'primera', 'auto'),
    (22, '26750017', 'federated', 'alevin', 'male', 'league', 'first', 'unknown', 'auto'),
    (22, '28448881', 'federated', 'alevin', 'male', 'cup', 'final', 'unknown', 'auto'),
    (22, '28416516', 'federated', 'alevin', 'male', 'cup', 'preliminary', 'unknown', 'auto'),
    (22, '26750019', 'federated', 'benjamin', 'male', 'league', 'first', 'unknown', 'auto'),
    (22, '28418732', 'federated', 'benjamin', 'male', 'cup', 'final', 'unknown', 'auto'),
    (22, '28418643', 'federated', 'benjamin', 'male', 'cup', 'preliminary', 'unknown', 'auto'),
    (22, '26738225', 'federated', 'benjamin', 'male', 'league', 'regular', 'unknown', 'auto'),
    (22, '26738294', 'federated', 'senior', 'female', 'tournament', 'unknown', 'segunda', 'auto'),
    (22, '26738218', 'federated', 'unknown', 'female', 'league', 'regular', 'primera_autonomica', 'needs_review'),
    (22, '28420731', 'federated', 'senior', 'female', 'cup', 'final', 'preferente', 'auto'),
    (22, '28420672', 'federated', 'senior', 'female', 'cup', 'preliminary', 'preferente', 'auto'),
    (22, '26738229', 'federated', 'unknown', 'female', 'league', 'first', 'preferente', 'needs_review'),
    (22, '28411135', 'federated', 'juvenil', 'female', 'cup', 'group_stage', 'unknown', 'auto'),
    (22, '26750020', 'federated', 'juvenil', 'female', 'league', 'regular', 'unknown', 'auto'),
    (22, '26738222', 'federated', 'cadete', 'female', 'league', 'regular', 'unknown', 'auto'),
    (22, '28403612', 'federated', 'cadete', 'female', 'cup', 'final', 'unknown', 'auto'),
    (22, '28403619', 'federated', 'infantil', 'female', 'cup', 'final', 'unknown', 'auto'),
    (22, '28403618', 'federated', 'infantil', 'female', 'cup', 'preliminary', 'unknown', 'auto'),
    (22, '26738223', 'federated', 'infantil', 'female', 'league', 'first', 'unknown', 'auto'),
    (22, '26750025', 'federated', 'alevin', 'female', 'league', 'first', 'unknown', 'auto'),
    (22, '28403631', 'federated', 'alevin', 'female', 'cup', 'unknown', 'unknown', 'auto'),
    (22, '26738224', 'federated', 'benjamin', 'female', 'league', 'regular', 'unknown', 'auto'),
    (22, '28403654', 'federated', 'benjamin', 'female', 'cup', 'group_stage', 'unknown', 'auto'),
    (21, '24038110', 'other', 'universitario', 'female', 'tournament', 'unknown', 'unknown', 'auto'),
    (21, '26233668', 'other', 'universitario', 'male', 'tournament', 'second', 'unknown', 'auto'),
    (21, '24742717', 'federated', 'senior', 'male', 'tournament', 'final', 'segunda_b', 'auto'),
    (21, '24038093', 'federated', 'senior', 'male', 'league', 'regular', 'tercera', 'auto'),
    (21, '25664533', 'federated', 'senior', 'male', 'cup', 'final', 'primera_autonomica', 'auto'),
    (21, '24038111', 'federated', 'senior', 'male', 'league', 'regular', 'primera_autonomica', 'auto'),
    (21, '25667263', 'federated', 'senior', 'male', 'cup', 'final', 'primera', 'auto'),
    (21, '24400426', 'federated', 'senior', 'male', 'league', 'first', 'primera', 'auto'),
    (21, '24895877', 'federated', 'juvenil', 'male', 'tournament', 'final', 'division_honor', 'auto'),
    (21, '24270433', 'federated', 'juvenil', 'male', 'league', 'regular', 'primera_autonomica', 'auto'),
    (21, '25690286', 'federated', 'juvenil', 'male', 'cup', 'final', 'primera', 'auto'),
    (21, '24038101', 'federated', 'juvenil', 'male', 'league', 'regular', 'primera', 'auto'),
    (21, '24271081', 'federated', 'cadete', 'male', 'league', 'regular', 'preferente', 'auto'),
    (21, '25690742', 'federated', 'cadete', 'male', 'cup', 'preliminary', 'primera', 'auto'),
    (21, '24271323', 'federated', 'infantil', 'male', 'league', 'regular', 'primera_autonomica', 'auto'),
    (21, '25691652', 'federated', 'infantil', 'male', 'cup', 'final', 'primera', 'auto'),
    (21, '24388441', 'federated', 'alevin', 'male', 'league', 'first', 'unknown', 'auto'),
    (21, '25692429', 'federated', 'alevin', 'male', 'cup', 'final', 'unknown', 'auto'),
    (21, '24388617', 'federated', 'benjamin', 'male', 'league', 'first', 'unknown', 'auto'),
    (21, '25693229', 'federated', 'benjamin', 'male', 'cup', 'final', 'unknown', 'auto'),
    (21, '26658822', 'federated', 'benjamin', 'male', 'unknown', 'final', 'unknown', 'needs_review'),
    (21, '26633831', 'federated', 'prebenjamin', 'male', 'cup', 'final', 'unknown', 'auto'),
    (21, '24324161', 'federated', 'senior', 'female', 'tournament', 'unknown', 'segunda', 'auto'),
    (21, '25693796', 'federated', 'senior', 'female', 'cup', 'final', 'preferente', 'auto'),
    (21, '24038102', 'federated', 'unknown', 'female', 'league', 'first', 'preferente', 'needs_review'),
    (21, '25694074', 'federated', 'juvenil', 'female', 'cup', 'group_stage', 'unknown', 'auto'),
    (21, '25319411', 'federated', 'juvenil', 'female', 'league', 'regular', 'unknown', 'auto'),
    (21, '26259996', 'federated', 'cadete', 'female', 'league', 'second', 'unknown', 'auto'),
    (21, '25694209', 'federated', 'cadete', 'female', 'cup', 'preliminary', 'unknown', 'auto'),
    (21, '25694664', 'federated', 'infantil', 'female', 'cup', 'preliminary', 'unknown', 'auto'),
    (21, '26260032', 'federated', 'infantil', 'female', 'league', 'second', 'unknown', 'auto'),
    (21, '26260040', 'federated', 'alevin', 'female', 'league', 'second', 'unknown', 'auto'),
    (21, '24038097', 'federated', 'benjamin', 'female', 'league', 'regular', 'unknown', 'auto'),
    (21, '26688373', 'federated', 'benjamin', 'female', 'cup', 'final', 'unknown', 'auto'),
    (10, '18316467', 'other', 'universitario', 'unknown', 'league', 'second', 'unknown', 'needs_review'),
    (10, '18298373', 'federated', 'senior', 'male', 'league', 'regular', 'preferente', 'auto'),
    (10, '18329899', 'federated', 'juvenil', 'male', 'cup', 'unknown', 'preferente', 'auto'),
    (10, '18298384', 'federated', 'alevin', 'male', 'league', 'regular', 'unknown', 'auto'),
    (10, '18314445', 'federated', 'alevin', 'male', 'league', 'regular', 'unknown', 'auto'),
    (10, '18329778', 'federated', 'alevin', 'male', 'playoff', 'second', 'unknown', 'auto'),
    (10, '18314452', 'federated', 'alevin', 'male', 'league', 'second', 'unknown', 'auto'),
    (10, '18298385', 'federated', 'benjamin', 'male', 'league', 'regular', 'unknown', 'auto'),
    (10, '18314446', 'federated', 'benjamin', 'male', 'league', 'regular', 'unknown', 'auto'),
    (10, '18329781', 'federated', 'benjamin', 'male', 'playoff', 'second', 'unknown', 'auto'),
    (10, '18314456', 'federated', 'benjamin', 'male', 'league', 'second', 'unknown', 'auto'),
    (10, '18329947', 'federated', 'prebenjamin', 'male', 'cup', 'unknown', 'unknown', 'auto'),
    (10, '18298380', 'federated', 'unknown', 'female', 'league', 'regular', 'primera_autonomica', 'needs_review'),
    (10, '18298381', 'federated', 'unknown', 'female', 'league', 'regular', 'preferente', 'needs_review'),
    (10, '18298387', 'federated', 'juvenil', 'female', 'league', 'regular', 'unknown', 'auto'),
    (10, '18298388', 'federated', 'cadete', 'female', 'league', 'regular', 'unknown', 'auto'),
    (10, '18314447', 'federated', 'cadete', 'female', 'league', 'regular', 'unknown', 'auto'),
    (10, '18330449', 'federated', 'cadete', 'female', 'cup', 'unknown', 'unknown', 'auto'),
    (10, '18298389', 'federated', 'infantil', 'female', 'league', 'regular', 'unknown', 'auto'),
    (10, '18298390', 'federated', 'alevin', 'female', 'league', 'regular', 'unknown', 'auto'),
    (10, '18330451', 'federated', 'alevin', 'female', 'cup', 'unknown', 'unknown', 'auto'),
    (21, '26689762', 'school', 'alevin', 'unknown', 'league', 'unknown', 'unknown', 'needs_review'),
    (21, '26629276', 'school', 'alevin', 'unknown', 'unknown', 'final', 'unknown', 'needs_review'),
    (21, '26553499', 'school', 'cadete', 'unknown', 'league', 'regular', 'unknown', 'needs_review'),
]


def test_real_catalogs_have_195_competitions() -> None:
    assert [len(_load(s)) for s in (22, 21, 10)] == [50, 103, 42]


def test_table_has_at_least_sixty_cases() -> None:
    assert len(TABLE) >= 60


@pytest.mark.parametrize("row", TABLE, ids=lambda r: f"s{r[0]}-{r[1]}")
def test_real_competition_table(row: tuple[Any, ...]) -> None:
    season, code, *expected = row
    r = classify_competition(meta_of(REAL[(season, code)]), None, CFG)
    assert [r.value(a) for a in FIELDS] + [r.review_status] == expected, REAL[(season, code)]["nombre"]


def test_every_real_competition_classifies_without_exception_and_is_known_or_needs_review(capsys: pytest.CaptureFixture[str]) -> None:
    dist: Counter[tuple[str, str, str, str]] = Counter()
    for raw in REAL.values():
        r = classify_competition(meta_of(raw), None, CFG)
        key_known = all(r.value(a) != "unknown" for a in ("modality", "category", "gender", "kind"))
        assert (r.review_status == "auto") == (key_known and not r.conflicts), raw["nombre"]
        assert set(r.attrs) == set(ATTRS)
        for a in ATTRS:
            v = r.attrs[a]
            assert 0.0 <= v.confidence <= 1.0
            assert (v.value == "unknown") == (v.source == "none"), (raw["nombre"], a)
        dist[(r.value("scope"), r.value("category"), r.value("gender"), r.review_status)] += 1
    assert sum(dist.values()) == 195
    with capsys.disabled():
        print("\nDISTRIBUCION (scope, category, gender, review_status): n")
        for k, n in sorted(dist.items()):
            print(f"  {k}: {n}")
        print("  review_status:", dict(Counter(k[3] for k in dist.elements())))


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def test_global_invariants_on_real_catalogs() -> None:
    for raw in REAL.values():
        r = classify_competition(meta_of(raw), None, CFG)
        text = _fold(f"{raw['nombre']} {raw['NombreCategoria']}")
        if r.value("category") == "senior":  # 0 senior sin señal
            assert any(w in text for w in ("aficionado", "senior", "tercera division", "segunda division b",
                                           "segunda division de futbol femenino")), raw["nombre"]
        if r.value("gender") == "female":  # 0 female sin marca femenina
            assert "femenin" in text, raw["nombre"]
        if r.attrs["gender"].rule_id == "implicit_male":  # 0 male implícito fuera de federado, ni con marca femenina
            assert r.value("scope") == "federated" and r.value("category") != "unknown" and "femenin" not in text, raw["nombre"]
        if r.value("scope") != "federated":
            assert r.attrs["gender"].rule_id != "implicit_male"


def test_real_universitarias_without_mark_stay_unknown_gender() -> None:
    r = classify_competition(meta_of(REAL[(10, "18300108")]), None, CFG)  # «Liga Universitaria Primera Fase»
    assert (r.value("scope"), r.value("category"), r.value("gender"), r.review_status) == ("other", "universitario", "unknown", "needs_review")


def adv(name: str, **fields: str | None) -> Any:
    return classify_competition(CompetitionMeta(name, **fields), None, CFG)


def test_adversarial_cup_juvenil_femenina_fase_final_without_fields() -> None:
    r = adv("COPA JUVENIL FEMENINA – fase final")
    assert (r.value("category"), r.value("gender"), r.value("kind"), r.value("phase")) == ("juvenil", "female", "cup", "final")
    assert r.value("modality") == "unknown" and r.review_status == "needs_review"  # sin campo oficial: no se inventa la modalidad


def test_adversarial_cadete_name_vs_juvenil_category_is_conflict() -> None:
    r = adv("CADETE PREFERENTE FS", category_name="PREFERENTE JUVENIL SALA", game_type_code="3", category_group_name="FUTBOL SALA",
            format_code="2")
    assert r.value("category") == "unknown" and r.conflicts == ("category",) and r.review_status == "needs_review"
    assert r.value("gender") == "unknown"  # implicit_male exige categoría conocida


def test_adversarial_no_signal_is_all_unknown() -> None:
    r = adv("Competición sin señal alguna")
    assert all(r.value(a) == "unknown" for a in ATTRS)
    assert r.review_status == "needs_review" and all(v.source == "none" and v.confidence == 0 for v in r.attrs.values())
    r2 = adv("  ")
    assert all(r2.value(a) == "unknown" for a in ATTRS)


@pytest.mark.parametrize("name", ["PREBENJAMIN", "Copa Prebenjamin - F.S.", "PRE BENJAMIN FS", "prebenjamín sala"])
def test_prebenjamin_is_checked_before_benjamin(name: str) -> None:
    assert adv(name).value("category") == "prebenjamin"


def test_benjamin_iniciacion_is_benjamin() -> None:
    r = adv("BENJAMIN INICIACION FS", category_name="BENJAMIN INICIACION SALA", game_type_code="3", category_group_name="FUTBOL SALA",
            format_code="2")
    assert (r.value("category"), r.value("gender"), r.value("kind"), r.review_status) == ("benjamin", "male", "league", "auto")


def test_university_female_mark_only_in_name() -> None:
    r = adv("CAMPEONATO UNIVERSITARIO FEMENINO FS", category_name="Liga Universitaria Futbol Sala", game_type_code="3",
            category_group_name="OTRAS CATEGORÍAS COMPETICIONALES", format_code="2")
    assert (r.value("category"), r.value("gender"), r.attrs["gender"].source) == ("universitario", "female", "rule")
    assert adv("CAMPEONATO UNIVERSITARIO", category_name="Liga Universitaria Futbol Sala",
               category_group_name="OTRAS CATEGORÍAS COMPETICIONALES").value("gender") == "unknown"  # nunca male implícito fuera de federado


def test_school_fase_final_alevin_with_zonal_category() -> None:
    r = adv("FASE FINAL alevin NAVALAFUENTE/CABANILLAS FS -fase final- ", category_name="FASE ZONAL SALA", game_type_code="3",
            category_group_name="DEPORTE ESCOLAR FÚTBOL SALA", format_code="1")
    assert (r.value("scope"), r.value("category"), r.value("gender"), r.value("phase")) == ("school", "alevin", "unknown", "final")
    assert r.review_status == "needs_review"


def test_female_and_male_marks_conflict() -> None:
    r = adv("TROFEO MASCULINO Y FEMENINO", category_name="SEGUNDA DIVISIÓN B DE FÚTBOL SALA", category_group_name="FUTBOL SALA")
    assert r.value("gender") == "unknown" and "gender" in r.conflicts


def test_implicit_male_only_federated_with_known_category() -> None:
    fed = adv("PRIMERA JUVENIL FS", category_name="PRIMERA JUVENIL SALA", category_group_name="FUTBOL SALA", game_type_code="3")
    assert (fed.value("gender"), fed.attrs["gender"].rule_id, fed.attrs["gender"].confidence) == ("male", "implicit_male", 0.8)
    for group in ("DEPORTE ESCOLAR FÚTBOL SALA", "OTRAS CATEGORÍAS COMPETICIONALES", None):
        assert adv("PRIMERA JUVENIL FS", category_name="PRIMERA JUVENIL SALA", category_group_name=group).value("gender") == "unknown"
    assert adv("FS", category_group_name="FUTBOL SALA").value("gender") == "unknown"  # categoría desconocida


def test_kind_league_needs_format_and_no_other_mark() -> None:
    assert adv("LIGA X", format_code="2").value("kind") == "league"
    assert adv("LIGA X", format_code="1").value("kind") == "unknown"
    assert adv("LIGA X").value("kind") == "unknown"
    assert adv("COPA X", format_code="2").value("kind") == "cup"
    assert adv("COPA TROFEO X").value("kind") == "unknown"
    assert adv("TERCERA DIVISIÓN PLAYOFF ASCENSO", format_code="1").value("kind") == "playoff"
    assert adv("TORNEO X").value("kind") == "tournament"


def test_regular_phase_only_for_leagues() -> None:
    assert adv("LIGA X", format_code="2").value("phase") == "regular"
    assert adv("COPA X", format_code="1").value("phase") == "unknown"
    assert adv("X 1ª Fase", format_code="2").value("phase") == "first"
    assert adv("X 2ª fase FS", format_code="2").value("phase") == "second"
    assert adv("X fase de grupos", format_code="1").value("phase") == "group_stage"
    assert adv("Final campeones alevin").value("phase") == "final"


def test_division_table_by_category_name() -> None:
    expect = {"TERCERA DIVISIÓN DE FÚTBOL SALA": "tercera", "SEGUNDA DIVISIÓN B DE FÚTBOL SALA": "segunda_b",
              "DIVISIÓN DE HONOR JUVENIL DE FÚTBOL SALA": "division_honor", "PRIMERA DIVISION AUTONOMICA CADETE SALA": "primera_autonomica",
              "PREFERENTE INFANTIL SALA": "preferente", "PRIMERA AFICIONADO SALA": "primera",
              "SEGUNDA DIVISIÓN DE FÚTBOL FEMENINO SALA": "segunda", "SUPERLIGA": "superliga", "ALEVIN SALA": "unknown"}
    for cname, div in expect.items():
        assert adv("X", category_name=cname).value("division") == div, cname


def test_accents_case_and_spaces_are_normalised() -> None:
    a = adv("  copa  JUVENIL   femenina  ", category_name="primera   juvenil sala ")
    b = adv("COPA JUVENIL FEMENINA", category_name="PRIMERA JUVENIL SALA")
    assert {x: a.attrs[x] for x in ATTRS} == {x: b.attrs[x] for x in ATTRS}
    assert adv("DIVISIÓN DE HONOR", category_name="DIVISIÓN DE HONOR JUVENIL").value("division") == "division_honor"
    assert adv("DIVISION DE HONOR", category_name="DIVISION DE HONOR JUVENIL").value("division") == "division_honor"


def test_source_maps_and_unknown_codes() -> None:
    codes = {"1": "football11", "2": "football7", "3": "futsal", "4": "football5", "5": "beach"}
    for code, modality in codes.items():
        r = adv("X", game_type_code=code)
        assert (r.value("modality"), r.attrs["modality"].source) == (modality, "source_field")
    assert adv("X", game_type_code="99").value("modality") == "unknown"
    for group, scope in {"FUTBOL SALA": "federated", "DEPORTE ESCOLAR FÚTBOL SALA": "school",
                         "OTRAS CATEGORÍAS COMPETICIONALES": "other"}.items():
        assert adv("X", category_group_name=group).value("scope") == scope
    assert adv("X", category_group_name="OTRA COSA").value("scope") == "unknown"


def test_override_has_precedence_over_field_and_rule() -> None:
    meta = CompetitionMeta("COPA JUVENIL FEMENINA", "3", "1", "PRIMERA JUVENIL SALA", "0", "FUTBOL SALA", "1")
    r = classify_competition(meta, {"category": "cadete", "gender": "male", "bogus": "x"}, CFG)
    assert (r.value("category"), r.attrs["category"].source, r.attrs["category"].rule_id) == ("cadete", "override", "override")
    assert (r.value("gender"), r.attrs["gender"].confidence) == ("male", 1.0)
    assert r.value("kind") == "cup" and r.attrs["kind"].source == "rule"
    assert classify_competition(meta, {}, CFG).as_json() == classify_competition(meta, None, CFG).as_json()


def test_classification_is_deterministic_and_json_shaped() -> None:
    meta = meta_of(REAL[(22, "26738243")])
    first, second = classify_competition(meta, None, CFG), classify_competition(meta, None, CFG)
    assert first == second
    assert json.loads(json.dumps(first.as_json()))["gender"] == {"value": "male", "source": "rule", "rule_id": "implicit_male",
                                                                    "confidence": 0.8}


# --- prueba de oro: `domain.competitions.classify` conserva (kind, age) en los 14 nombres de CATALOG_SCOPE ---

def test_legacy_classify_golden_for_the_14_scope_names() -> None:
    golden = json.loads((FIXTURES / "classify_legacy_golden.json").read_text(encoding="utf-8"))
    assert len(golden) == len(CATALOG_SCOPE) == 14
    cfg = load_aliases()
    for _, name in CATALOG_SCOPE:
        assert classify(name, cfg) == Taxonomy(*golden[name]), name


def test_legacy_classify_default_age_is_unknown_but_default_kind_is_league() -> None:
    cfg = load_aliases()
    assert classify("Competición sin señal", cfg) == Taxonomy("league", "unknown")
    assert classify("ALEVIN FS", cfg).age == "alevin" and classify("PREBENJAMIN FS", cfg).age == "prebenjamin"
    assert classify("Primera Aficionado FS", cfg).age == "senior" and classify("TERCERA DIVISION FS", cfg).age == "senior"
    assert classify("Copa Juvenil Senior", cfg).age == "juvenil"
