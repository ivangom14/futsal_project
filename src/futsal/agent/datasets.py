"""Datasets de evaluación del router y del extractor de entidades (Fase 2A): esquema, huellas, solapes y guardia del Holdout B. Sin LLM, sin red.

Conjuntos (nunca se mezclan en las métricas):
- AJUSTE: `design_set`, `validation` (ya analizados: contaminados) y `entities_dev`: se pueden mirar y usar para depurar.
- HOLDOUT A (`tests/fixtures/router/holdout_a.json`): validación. Escrito antes de ejecutar nada sobre él. NO se usa para ajustar; se evalúa con huella sellada.
- HOLDOUT B (`data/holdout/holdout_b.json`, ignorado por Git; en el repositorio solo su manifiesto con huella y recuentos): reservado a la evaluación final.
  Cualquier lectura exige reconocerlo expresamente y queda anotada en `data/holdout/b_access.log`.

Cada pregunta lleva etiquetas de HERRAMIENTAS (`needed_tools`: mínimo para responder bien; `plausible_tools`: superconjunto aceptable para medir recall), no solo intención.

CLI: `python -m futsal.agent.datasets validate|stats|seal|verify|overlap|add ...`
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from futsal.agent.preflight import EXPECTED_TOOLS
from futsal.agent.router import INTENTS

SOURCES = ("blind", "real_anonymized")
ABSTAIN_REASONS = ("no_team", "homonym", "multi_team", "not_in_catalog", "rules", "historical", "context", "partial_name", "generic_word", "player", "off_topic")
AMBIGUITY = ("none", "multi_group", "multi_competition", "unknown")
SCOPES = ("resolved", "needs_clarification", "missing", "n/a")
SEASONS = ("current", "historical")
REQUIRED = ("id", "question", "source", "category", "intent", "needed_tools", "plausible_tools", "prefetch_expected", "prefetch_entity", "abstain_reason",
            "entity_ambiguity", "scope", "followup", "prev_questions", "focus", "tier0_eligible", "season")
HOLDOUT_B_PATH = Path("data/holdout/holdout_b.json")
B_ACCESS_LOG = Path("data/holdout/b_access.log")
NEAR_DUPLICATE = 0.85
MAX_QUESTION = 400


def tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9áéíóúñü]+", text.lower()))


def key(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9áéíóúñü]+", text.lower()))


def load(path: Path) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return doc


def validate(doc: dict[str, Any]) -> list[str]:
    """Errores de esquema y de coherencia de etiquetas (lista vacía = válido)."""
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_q: set[str] = set()
    for i, c in enumerate(doc.get("cases", [])):
        cid = str(c.get("id", f"#{i}"))
        miss = [k for k in REQUIRED if k not in c]
        if miss:
            errors.append(f"{cid}: faltan campos {miss}")
            continue
        if cid in seen_ids:
            errors.append(f"{cid}: id repetido")
        seen_ids.add(cid)
        q = str(c["question"]).strip()
        if not q or len(q) > MAX_QUESTION:
            errors.append(f"{cid}: pregunta vacía o de más de {MAX_QUESTION} caracteres")
        if key(q) in seen_q:
            errors.append(f"{cid}: pregunta repetida dentro del conjunto")
        seen_q.add(key(q))
        if c["source"] not in SOURCES:
            errors.append(f"{cid}: source {c['source']!r}")
        if c["intent"] not in INTENTS:
            errors.append(f"{cid}: intención {c['intent']!r}")
        for field in ("needed_tools", "plausible_tools"):
            bad = [t for t in c[field] if t not in EXPECTED_TOOLS]
            if bad:
                errors.append(f"{cid}: {field} con herramientas desconocidas {bad}")
        if not set(c["needed_tools"]) <= set(c["plausible_tools"]):
            errors.append(f"{cid}: needed_tools debe ser subconjunto de plausible_tools")
        if c["prefetch_expected"] not in ("fire", "abstain"):
            errors.append(f"{cid}: prefetch_expected {c['prefetch_expected']!r}")
        elif c["prefetch_expected"] == "fire" and (not c["prefetch_entity"] or c["abstain_reason"]):
            errors.append(f"{cid}: «fire» exige prefetch_entity y sin abstain_reason")
        elif c["prefetch_expected"] == "abstain" and (c["prefetch_entity"] or c["abstain_reason"] not in ABSTAIN_REASONS):
            errors.append(f"{cid}: «abstain» exige abstain_reason válido y sin prefetch_entity")
        if c["entity_ambiguity"] not in AMBIGUITY or c["scope"] not in SCOPES or c["season"] not in SEASONS:
            errors.append(f"{cid}: ambigüedad, ámbito o temporada no válidos")
        if c["followup"] and not c["prev_questions"]:
            errors.append(f"{cid}: un seguimiento necesita prev_questions")
        if c["season"] == "historical" and (c["tier0_eligible"] or c["prefetch_expected"] != "abstain"):
            errors.append(f"{cid}: una consulta histórica no es elegible ni dispara prefetch (fuera de alcance inicial)")
    return errors


def stats(doc: dict[str, Any]) -> dict[str, Any]:
    cases = doc.get("cases", [])
    return {"n": len(cases), "prefetch_expected": dict(Counter(c["prefetch_expected"] for c in cases)),
            "abstain_reason": dict(Counter(c["abstain_reason"] for c in cases if c["abstain_reason"])),
            "source": dict(Counter(c["source"] for c in cases)), "intent": dict(Counter(c["intent"] for c in cases)),
            "category": dict(Counter(c["category"] for c in cases)), "tier0_eligible": sum(bool(c["tier0_eligible"]) for c in cases),
            "followups": sum(bool(c["followup"]) for c in cases), "historical": sum(c["season"] == "historical" for c in cases)}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_for(doc: dict[str, Any], path: Path, note: str = "") -> dict[str, Any]:
    s = stats(doc)
    return {"name": path.stem, "sha256": sha256_file(path), "n": s["n"], "counts": {k: s[k] for k in ("prefetch_expected", "abstain_reason", "source", "intent")},
            "sealed_at": datetime.now(UTC).isoformat(timespec="seconds"), "note": note, "history": []}


def seal(path: Path, manifest_path: Path, note: str = "") -> dict[str, Any]:
    doc = load(path)
    errors = validate(doc)
    if errors:
        raise ValueError(f"no se sella un dataset inválido: {errors[:5]}")
    manifest = manifest_for(doc, path, note)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def verify(path: Path, manifest_path: Path) -> tuple[bool, str]:
    """¿El fichero es el sellado? Un cambio posterior (aunque sea una coma) se detecta."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = sha256_file(path)
    return actual == manifest["sha256"], f"huella {actual[:12]} {'=' if actual == manifest['sha256'] else '≠'} {manifest['sha256'][:12]} ({manifest['n']} preguntas)"


def overlaps(a: dict[str, Any], b: dict[str, Any], threshold: float = NEAR_DUPLICATE) -> list[tuple[str, str, float]]:
    """Pares (id de a, id de b, similitud) idénticos o casi idénticos (Jaccard de palabras ≥ umbral). Los holdouts no deben compartir preguntas con el ajuste."""
    bq = [(c["id"], key(c["question"]), tokens(c["question"])) for c in b.get("cases", [])]
    found = []
    for c in a.get("cases", []):
        ka, ta = key(c["question"]), tokens(c["question"])
        for bid, kb, tb in bq:
            if ka == kb:
                found.append((c["id"], bid, 1.0))
            elif ta and tb:
                sim = len(ta & tb) / len(ta | tb)
                if sim >= threshold:
                    found.append((c["id"], bid, round(sim, 3)))
    return found


def load_holdout_b(path: Path = HOLDOUT_B_PATH, *, acknowledge: bool, purpose: str, log_path: Path = B_ACCESS_LOG) -> dict[str, Any]:
    """Lectura del Holdout B: reservado a la evaluación final. Sin reconocimiento expreso lanza `PermissionError`; cada lectura queda anotada."""
    if not acknowledge:
        raise PermissionError("El Holdout B está reservado a la evaluación final y no se usa para ajustar el router ni el extractor. "
                              "Si de verdad es la evaluación final, reconócelo expresamente (--use-holdout-b-final).")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": datetime.now(UTC).isoformat(timespec="seconds"), "purpose": purpose, "sha256": sha256_file(path)}, ensure_ascii=False) + "\n")
    return load(path)


def add_cases(path: Path, new_cases: list[dict[str, Any]], manifest_path: Path, against: list[Path] | None = None, note: str = "") -> dict[str, Any]:
    """Incorpora preguntas PROPIAS ya etiquetadas a un dataset. Valida esquema, rechaza duplicados (también frente a otros conjuntos) y re-sella dejando constancia."""
    doc = load(path)
    before = len(doc["cases"])
    merged = {**doc, "cases": [*doc["cases"], *new_cases]}
    errors = validate(merged)
    if errors:
        raise ValueError(f"preguntas no válidas: {errors[:5]}")
    for other in against or []:
        clash = overlaps({"cases": new_cases}, load(other))
        if clash:
            raise ValueError(f"solapan con {other.name}: {clash[:3]}")
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"history": []}
    manifest = manifest_for(merged, path, note or old.get("note", ""))
    manifest["history"] = [*old.get("history", []), {"date": datetime.now(UTC).isoformat(timespec="seconds"), "action": "add", "added": len(new_cases),
                                                        "n_before": before, "sha256": manifest["sha256"], "note": note}]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Datasets de evaluación (sin LLM ni red).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("validate", "stats"):
        sub.add_parser(name).add_argument("file")
    s = sub.add_parser("seal")
    s.add_argument("file")
    s.add_argument("--manifest", required=True)
    s.add_argument("--note", default="")
    v = sub.add_parser("verify")
    v.add_argument("file")
    v.add_argument("--manifest", required=True)
    o = sub.add_parser("overlap")
    o.add_argument("a")
    o.add_argument("b")
    a = sub.add_parser("add")
    a.add_argument("--into", required=True)
    a.add_argument("--manifest", required=True)
    a.add_argument("--from", dest="source", required=True, help="JSON con {\"cases\": [...]} ya etiquetados")
    a.add_argument("--against", nargs="*", default=[])
    a.add_argument("--note", default="")
    args = ap.parse_args(argv)
    if args.cmd == "validate":
        errors = validate(load(Path(args.file)))
        print("válido" if not errors else "\n".join(errors[:30]))
        return 1 if errors else 0
    if args.cmd == "stats":
        print(json.dumps(stats(load(Path(args.file))), ensure_ascii=False, indent=1))
    elif args.cmd == "seal":
        m = seal(Path(args.file), Path(args.manifest), args.note)
        print(f"sellado: {m['n']} preguntas, sha256 {m['sha256'][:16]}…")
    elif args.cmd == "verify":
        ok, msg = verify(Path(args.file), Path(args.manifest))
        print(("OK " if ok else "CAMBIADO ") + msg)
        return 0 if ok else 1
    elif args.cmd == "overlap":
        found = overlaps(load(Path(args.a)), load(Path(args.b)))
        print(f"{len(found)} solapes" + (f": {found[:10]}" if found else ""))
        return 1 if found else 0
    else:
        m = add_cases(Path(args.into), load(Path(args.source))["cases"], Path(args.manifest), [Path(p) for p in args.against], args.note)
        print(f"añadidas; ahora {m['n']} preguntas, sha256 {m['sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
