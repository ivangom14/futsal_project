"""Carga de la batería `evals/datasets/futsito-v1`: divisiones dev/holdout, sellado con huellas y suite `smoke40`.

`agent.datasets.seal` valida el esquema JSON de los datasets del router y no sirve para YAML de casos: aquí se reutiliza su
huella (`sha256_file`) y el mismo criterio (manifiesto con huella; un cambio posterior, aunque sea una coma, se detecta).
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from futsal.agent.datasets import sha256_file
from futsal.evals.model import STRATA, Case, CaseError, parse_case

DATASET_DIR = Path("evals/datasets/futsito-v1")
SEALS_FILE = "SEALS.json"
SMOKE_FILE = "SMOKE40.json"
SPLITS = ("dev", "holdout", "all")
SUITES = ("regression", "futsito-v1", "smoke40")
HOLDOUT_SUFFIX = ".holdout.yaml"


class SealError(RuntimeError):
    pass


def dataset_dir(root: Path = Path(".")) -> Path:
    return root / DATASET_DIR


def _read(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise CaseError(f"{path.name}: se esperaba una lista de casos")
    cases = [parse_case(c, path.name) for c in raw]
    holdout_file = path.name.endswith(HOLDOUT_SUFFIX)
    for c in cases:
        if c.holdout != holdout_file:
            raise CaseError(f"{c.id}: holdout={c.holdout} no coincide con el fichero {path.name}")
    return cases


def seal_files(root: Path = Path(".")) -> list[Path]:
    return sorted(dataset_dir(root).glob(f"*{HOLDOUT_SUFFIX}"))


def seal_holdouts(root: Path = Path("."), note: str = "") -> dict[str, Any]:
    """Escribe `SEALS.json` con la huella de cada fichero de holdout (se hace UNA vez, al cerrar el dataset)."""
    files: dict[str, Any] = {}
    for path in seal_files(root):
        files[path.name] = {"sha256": sha256_file(path), "cases": len(_read(path))}
    manifest = {"schema": 1, "sealed_at": datetime.now(UTC).isoformat(timespec="seconds"), "note": note, "files": files}
    (dataset_dir(root) / SEALS_FILE).write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return manifest


def verify_seals(root: Path = Path(".")) -> list[str]:
    """Problemas del sellado (lista vacía = íntegro): fichero cambiado, añadido o desaparecido."""
    seals_path = dataset_dir(root) / SEALS_FILE
    if not seals_path.exists():
        return [f"falta {SEALS_FILE}: el holdout no está sellado"]
    sealed: dict[str, Any] = json.loads(seals_path.read_text(encoding="utf-8"))["files"]
    problems = []
    present = {p.name: p for p in seal_files(root)}
    for name, info in sealed.items():
        if name not in present:
            problems.append(f"{name}: sellado pero ausente")
        elif sha256_file(present[name]) != info["sha256"]:
            problems.append(f"{name}: la huella no coincide con la sellada (fichero modificado tras sellarlo)")
    problems += [f"{n}: fichero de holdout sin sellar" for n in present if n not in sealed]
    return problems


def load_cases(root: Path = Path("."), split: str = "dev") -> list[Case]:
    """Casos de la división pedida. Con `dev` NO se abre ningún fichero de holdout; con holdout se verifica el sellado."""
    if split not in SPLITS:
        raise ValueError(f"split {split!r} no válido ({SPLITS})")
    ddir = dataset_dir(root)
    cases: list[Case] = []
    for path in sorted(ddir.glob("*.yaml")):
        is_holdout = path.name.endswith(HOLDOUT_SUFFIX)
        if (is_holdout and split == "dev") or (not is_holdout and split == "holdout"):
            continue
        cases += _read(path)
    if split in ("holdout", "all"):
        problems = verify_seals(root)
        if problems:
            raise SealError("holdout no íntegro: " + "; ".join(problems))
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise CaseError("ids de caso repetidos: " + ", ".join(sorted({i for i in ids if ids.count(i) > 1})))
    return sorted(cases, key=lambda c: (c.stratum, c.id))


def smoke_ids(root: Path = Path(".")) -> list[str]:
    ids: list[str] = json.loads((dataset_dir(root) / SMOKE_FILE).read_text(encoding="utf-8"))["cases"]
    return ids


def suite_cases(suite: str, split: str = "dev", root: Path = Path(".")) -> list[Case]:
    if suite == "futsito-v1":
        return load_cases(root, split)
    if suite == "smoke40":
        if split != "dev":
            raise ValueError("smoke40 es un subconjunto de dev: no admite --split holdout/all")
        wanted = smoke_ids(root)
        by_id = {c.id: c for c in load_cases(root, "dev")}
        missing = [i for i in wanted if i not in by_id]
        if missing:
            raise CaseError(f"smoke40 referencia casos que no están en dev: {missing[:5]}")
        return [by_id[i] for i in wanted]
    raise ValueError(f"suite {suite!r} no es una batería de casos YAML")


def counts(cases: list[Case]) -> dict[str, dict[str, int]]:
    """Casos y turnos por estrato (para `plan` y la entrega)."""
    out: dict[str, dict[str, int]] = {s: {"cases": 0, "turns": 0, "holdout": 0} for s in STRATA}
    for c in cases:
        out[c.stratum]["cases"] += 1
        out[c.stratum]["turns"] += len(c.turns)
        out[c.stratum]["holdout"] += int(c.holdout)
    return {s: v for s, v in out.items() if v["cases"]}
