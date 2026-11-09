"""Modelo de caso de Futsito-Eval v1 (docs/v2/evaluation.md §3) y su validación."""

from dataclasses import dataclass
from typing import Any

STRATA = {
    "S1": "Sencillas", "S2": "Ambiguas", "S3": "Encadenadas", "S4": "Complejas", "S5": "Sin datos",
    "S6": "Adversariales", "S7": "Multicompetición", "S8": "Estadísticas", "S9": "Documentales"}
BEHAVIORS = ("answer", "clarify", "abstain", "offtopic")
CAPABILITIES = ("v1", "v2")
FACT_KINDS = ("number", "score", "name", "names", "date", "text_any", "position")
EXPECT_KEYS = {"behavior", "facts", "forbid_facts", "candidate_facts", "clarify_mentions", "tools", "max_llm_calls",
               "forbid_scores", "forbid_any_score", "forbid_phrases", "forbid_tools", "source", "rule_case", "ignore_decoys", "note"}
SELECT_KINDS = ("team", "group", "competition", "club", "match", "season_absent", "fictional_team", "fictional_competition",
                "fictional_player", "category_absent")


class CaseError(ValueError):
    pass


@dataclass(frozen=True)
class TurnSpec:
    user: str
    expect: dict[str, Any]


@dataclass(frozen=True)
class Case:
    id: str
    stratum: str
    holdout: bool
    requires: tuple[str, ...]
    capability: str
    select: dict[str, dict[str, Any]]
    turns: tuple[TurnSpec, ...]
    source_file: str = ""

    @property
    def conversation(self) -> bool:
        return len(self.turns) > 1


def parse_case(raw: dict[str, Any], source: str = "") -> Case:
    cid = str(raw.get("id", ""))
    if not cid:
        raise CaseError(f"{source}: caso sin id")
    for key in ("stratum", "holdout", "requires", "capability", "select", "turns"):
        if key not in raw:
            raise CaseError(f"{cid}: falta «{key}»")
    if raw["stratum"] not in STRATA:
        raise CaseError(f"{cid}: estrato {raw['stratum']!r} desconocido")
    if raw["capability"] not in CAPABILITIES:
        raise CaseError(f"{cid}: capability {raw['capability']!r}")
    select = raw["select"] or {}
    for name, spec in select.items():
        if not isinstance(spec, dict) or spec.get("kind") not in SELECT_KINDS:
            raise CaseError(f"{cid}: select.{name} necesita kind en {SELECT_KINDS}")
    turns = []
    for i, t in enumerate(raw["turns"] or []):
        if not isinstance(t, dict) or not t.get("user") or not isinstance(t.get("expect"), dict):
            raise CaseError(f"{cid}: turno {i + 1} necesita user y expect")
        exp = t["expect"]
        if exp.get("behavior") not in BEHAVIORS:
            raise CaseError(f"{cid}: turno {i + 1}: behavior {exp.get('behavior')!r} no válido ({BEHAVIORS})")
        unknown = set(exp) - EXPECT_KEYS
        if unknown:
            raise CaseError(f"{cid}: turno {i + 1}: claves de expect desconocidas {sorted(unknown)}")
        for f in [*(exp.get("facts") or []), *(exp.get("forbid_facts") or []), *(exp.get("candidate_facts") or [])]:
            if f.get("kind") not in FACT_KINDS:
                raise CaseError(f"{cid}: turno {i + 1}: hecho de tipo {f.get('kind')!r} no válido ({FACT_KINDS})")
        turns.append(TurnSpec(str(t["user"]), exp))
    if not turns:
        raise CaseError(f"{cid}: sin turnos")
    if raw["stratum"] == "S3" and len(turns) < 2:
        raise CaseError(f"{cid}: S3 son conversaciones (≥ 2 turnos)")
    return Case(cid, str(raw["stratum"]), bool(raw["holdout"]), tuple(str(r) for r in raw["requires"] or []),
                str(raw["capability"]), select, tuple(turns), source)
