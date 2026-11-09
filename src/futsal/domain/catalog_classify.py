"""Clasificación de competiciones del catálogo (revisión A2): puro y determinista, sin E/S.

Por atributo (modality, scope, category, gender, kind, phase, division) devuelve valor, origen
(override | source_field | rule | none), regla y confianza. Precedencia: override > campo oficial > regla > unknown.
Sin señal suficiente o con conflicto el valor es `unknown` y la competición queda en `needs_review`: nunca hay valores por defecto
inventados (la única excepción explícita y trazable es la regla `implicit_male`, solo para el ámbito federado).
Las correspondencias y reglas viven por fuente en `config/sources/<fuente>.json`.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from futsal.domain.competitions import fold

UNKNOWN = "unknown"
ATTRS = ("modality", "scope", "category", "gender", "kind", "phase", "division")
OVERRIDE, SOURCE_FIELD, RULE, NONE = "override", "source_field", "rule", "none"
AUTO, NEEDS_REVIEW = "auto", "needs_review"
KEY_ATTRS = ("modality", "category", "gender", "kind")


@dataclass(frozen=True)
class CompetitionMeta:
    """Nombre y campos oficiales de una competición tal como los da la fuente (todos opcionales salvo el nombre)."""

    name: str
    game_type_code: str | None = None
    category_code: str | None = None
    category_name: str | None = None
    category_group_code: str | None = None
    category_group_name: str | None = None
    format_code: str | None = None
    active: bool | None = None


@dataclass(frozen=True)
class AttrValue:
    value: str = UNKNOWN
    source: str = NONE
    rule_id: str | None = None
    confidence: float = 0.0

    def as_json(self) -> dict[str, Any]:
        return {"value": self.value, "source": self.source, "rule_id": self.rule_id, "confidence": self.confidence}


@dataclass(frozen=True)
class Classification:
    attrs: Mapping[str, AttrValue]
    review_status: str
    conflicts: tuple[str, ...] = ()

    def value(self, attr: str) -> str:
        return self.attrs[attr].value

    def as_json(self) -> dict[str, dict[str, Any]]:
        return {a: self.attrs[a].as_json() for a in ATTRS}


def _scan(text: str, rules: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """(valor, id de regla) de las reglas que casan, en orden de regla; lo reconocido se consume para que
    `prebenjamin` no active `benjamin`. Las frases casan al inicio de palabra (prefijo)."""
    found: list[tuple[str, str]] = []
    for rule in rules:
        for phrase in rule["phrases"]:
            pattern = rf"(?<![a-z0-9]){re.escape(phrase)}[a-z0-9]*"
            if re.search(pattern, text):
                found.append((rule["value"], rule["id"]))
                text = re.sub(pattern, " ", text)
                break
    return found


def _has(text: str, phrases: Sequence[str]) -> str | None:
    for p in phrases:
        if re.search(rf"(?<![a-z0-9]){re.escape(p)}", text):
            return p
    return None


def classify_competition(meta: CompetitionMeta, overrides: Mapping[str, Any] | None, cfg: Mapping[str, Any]) -> Classification:
    conf = cfg["confidence"]
    name, cname = fold(meta.name or ""), fold(meta.category_name or "")
    out: dict[str, AttrValue] = {}
    conflicts: list[str] = []
    ov = {a: str(v) for a, v in (overrides or {}).items() if a in ATTRS and v}

    def take(attr: str) -> bool:
        if attr in ov:
            out[attr] = AttrValue(ov[attr], OVERRIDE, "override", conf["override"])
            return True
        return False

    # scope y modality: tablas de correspondencia por fuente (campo oficial)
    if not take("scope"):
        groups = {fold(k): v for k, v in cfg["category_group_name"].items()}
        scope = groups.get(fold(meta.category_group_name or ""))
        out["scope"] = (AttrValue(scope, SOURCE_FIELD, "category_group_name", conf["source_map"]) if scope else AttrValue())
    if not take("modality"):
        modality = cfg["game_type_code"].get((meta.game_type_code or "").strip())
        if modality:
            out["modality"] = AttrValue(modality, SOURCE_FIELD, "game_type_code", conf["source_map"])
        elif name and (_has(name, cfg.get("modality_name_phrases", ())) or set(name.split()) & set(cfg.get("modality_name_tokens", ()))):
            out["modality"] = AttrValue("futsal", RULE, "name:futsal", conf["modality_name"])
        else:
            out["modality"] = AttrValue()

    # category: category_name y después el nombre; discrepancia => unknown + revisión
    if not take("category"):
        from_cat = _scan(cname, cfg["category_rules"])
        from_name = _scan(name, cfg["category_rules"])
        vals_c, vals_n = {v for v, _ in from_cat}, {v for v, _ in from_name}
        if len(vals_c) > 1 or len(vals_n) > 1 or (vals_c and vals_n and vals_c != vals_n):
            conflicts.append("category")
            out["category"] = AttrValue()
        elif from_cat:
            out["category"] = AttrValue(from_cat[0][0], SOURCE_FIELD, f"category_name:{from_cat[0][1]}", conf["category_name"])
        elif from_name:
            out["category"] = AttrValue(from_name[0][0], RULE, f"name:{from_name[0][1]}", conf["name_rule"])
        else:
            out["category"] = AttrValue()

    # gender: marca explícita; implicit_male solo en federado con categoría conocida y sin marca femenina
    if not take("gender"):
        fem_c, fem_n = _has(cname, cfg["female_phrases"]), _has(name, cfg["female_phrases"])
        mal_c, mal_n = _has(cname, cfg["male_phrases"]), _has(name, cfg["male_phrases"])
        female, male = bool(fem_c or fem_n), bool(mal_c or mal_n)
        if female and male:
            conflicts.append("gender")
            out["gender"] = AttrValue()
        elif female:
            out["gender"] = (AttrValue("female", SOURCE_FIELD, "category_name:femenin", conf["category_name"]) if fem_c
                             else AttrValue("female", RULE, "name:femenin", conf["name_rule"]))
        elif male:
            out["gender"] = (AttrValue("male", SOURCE_FIELD, "category_name:masculin", conf["category_name"]) if mal_c
                             else AttrValue("male", RULE, "name:masculin", conf["name_rule"]))
        elif out["scope"].value == "federated" and out["category"].value != UNKNOWN:
            out["gender"] = AttrValue("male", RULE, "implicit_male", conf["implicit_male"])
        else:
            out["gender"] = AttrValue()

    # kind: marcas del nombre; liga solo por formato oficial y sin otra marca
    if not take("kind"):
        marks = _scan(name, cfg["kind_rules"])
        if len(marks) > 1:
            conflicts.append("kind")
            out["kind"] = AttrValue()
        elif marks:
            out["kind"] = AttrValue(marks[0][0], RULE, f"name:{marks[0][1]}", conf["name_rule"])
        elif cfg["format_code"].get((meta.format_code or "").strip()) == "league":
            out["kind"] = AttrValue("league", SOURCE_FIELD, "format_code:league", conf["format_league"])
        else:
            out["kind"] = AttrValue()

    # phase: marcas del nombre; «regular» por defecto solo en ligas
    if not take("phase"):
        marks = _scan(name, cfg["phase_rules"])
        if len(marks) > 1:
            conflicts.append("phase")
            out["phase"] = AttrValue()
        elif marks:
            out["phase"] = AttrValue(marks[0][0], RULE, f"name:{marks[0][1]}", conf["name_rule"])
        elif out["kind"].value == "league":
            out["phase"] = AttrValue("regular", RULE, "default_regular_league", conf["default_regular"])
        else:
            out["phase"] = AttrValue()

    # division: tabla normalizada sobre category_name
    if not take("division"):
        div = _scan(cname, cfg["division_rules"])
        out["division"] = AttrValue(div[0][0], SOURCE_FIELD, f"division:{div[0][1]}", conf["category_name"]) if div else AttrValue()

    known = all(out[a].value != UNKNOWN for a in KEY_ATTRS)
    return Classification(out, AUTO if known and not conflicts else NEEDS_REVIEW, tuple(conflicts))
