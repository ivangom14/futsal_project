"""Evaluación ciega para Opus (desde Claude Code, sin API): paquete anonimizado, hoja de puntuación y revelado posterior.

`blind/` dentro de la ejecución:
- `PROMPT.md`: instrucciones para el juez (criterios, reglas y procedimiento). No contiene identidades.
- `items.md` / `items.json`: cada conversación con la MISMA evidencia (verdad del snapshot) y las respuestas etiquetadas A, B, C…;
  la asignación letra→modelo se baraja por conversación con semilla reproducible. Sin veredictos deterministas, costes ni tiempos.
- `scoresheet.json`: plantilla que rellena el juez.
- `key.json`: la clave (letra→modelo por conversación). NO se abre hasta terminar la valoración.
`reveal` une la hoja con la clave, la contrasta con los veredictos deterministas y añade costes y latencias medidos.
"""

import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from futsal.evals.bench.analysis import CLASSES, GRADED, analyze
from futsal.evals.bench.runner import load_run

BLIND_DIR = "blind"
CRITERIA = (
    ("exactitud", "Exactitud factual: cada cifra, resultado, fecha, nombre y puesto coincide con la evidencia"),
    ("sin_alucinacion", "Ausencia de alucinaciones: nada que la evidencia no respalde; no rellena huecos con suposiciones"),
    ("entidad", "Selección correcta de equipo, competición, grupo, categoría y temporada"),
    ("intencion", "Comprensión de la intención: responde a lo que se pregunta, ni más ni menos"),
    ("uso_mcp", "Uso correcto de las herramientas: las necesarias, sin llamadas absurdas"),
    ("ambiguedad", "Gestión de la ambigüedad o de la falta de datos: aclara o se abstiene cuando corresponde"),
    ("coherencia", "Coherencia en conversaciones encadenadas: mantiene el contexto sin heredarlo indebidamente"),
    ("claridad", "Claridad: directa y comprensible para un aficionado (la extensión NO suma)"),
)
EVIDENCE_KEYS = ("behavior", "facts", "forbid_facts", "clarify_mentions", "forbid_phrases", "forbid_scores", "forbid_any_score",
                 "tools_needed", "tools_allowed", "scope_requested", "scope_other")
CLASS_RULES = {
    "correcta": "conducta esperada y todos los hechos requeridos, sin nada sin respaldo (la redacción da igual)",
    "parcial": "conducta correcta pero falta o está mal presentado algún hecho requerido",
    "incorrecta": "conducta equivocada (responde cuando debía aclarar/abstenerse, se abstiene cuando había datos, responde a otra cosa)",
    "alucinacion": "afirma algo que contradice la evidencia o no está en ella (cifra, marcador, nombre, regla) o usa datos de otra competición/grupo/temporada",
    "error_tecnico": "no hay respuesta útil por un fallo técnico (error, mensaje de sistema, respuesta vacía)",
    "no_evaluable": "la evidencia no basta para juzgar",
}


def _evidence(expected: dict[str, Any]) -> dict[str, Any]:
    return {k: expected[k] for k in EVIDENCE_KEYS if k in expected and expected[k] not in (None, [], "", False)}


def build_blind(run_dir: Path, seed: int = 7, rep: int = 0) -> dict[str, Any]:
    meta, results = load_run(run_dir)
    if len(results) < 2:
        raise ValueError("la evaluación ciega necesita al menos dos modelos con resultados")
    a = analyze(meta, results)
    rows = a["rows"]
    models = sorted(results)
    letters = [chr(ord("A") + i) for i in range(len(models))]
    rng = random.Random(seed)
    items: list[dict[str, Any]] = []
    key: dict[str, dict[str, str]] = {}
    any_res = results[models[0]]
    for n, cid in enumerate(meta["case_ids"], 1):
        expected = any_res["cases"].get(cid, {}).get("expected", [])
        per_model = {m: sorted((r for r in rows[m] if r["case_id"] == cid and r["rep"] == rep), key=lambda r: r["turn"]) for m in models}
        if not any(per_model.values()) or any(not v for v in per_model.values()):
            continue
        order = models[:]
        rng.shuffle(order)
        item_id = f"I{n:02d}"
        key[item_id] = dict(zip(letters, order, strict=True))
        turns = [{"turn": i + 1, "question": t.get("user", ""), "evidence": _evidence(t)} for i, t in enumerate(expected)]
        responses = {}
        for letter, m in zip(letters, order, strict=True):
            responses[letter] = [{"turn": r["turn"] + 1, "answer": r["answer"] or "(sin respuesta)", "tools": r["tools_used"]} for r in per_model[m]]
        items.append({"item": item_id, "category": meta["cases"][cid]["category"], "turns": turns, "responses": responses})
    out = run_dir / BLIND_DIR
    out.mkdir(exist_ok=True)
    sheet = {"instructions": "Rellena class (una de las clases) y scores (0 = mal, 1 = a medias, 2 = bien, null = no aplica) para cada letra; "
                             "justification breve; comparison: diferencias relevantes entre letras. No abras key.json.",
             "classes": list(CLASS_RULES), "criteria": [c for c, _ in CRITERIA],
             "items": [{"item": it["item"], "labels": {letter: {"class": None, "scores": {c: None for c, _ in CRITERIA}, "justification": ""}
                                                       for letter in it["responses"]}, "comparison": ""} for it in items]}
    (out / "items.json").write_text(json.dumps({"run_id": meta["run_id"], "seed": seed, "rep": rep, "items": items}, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")
    (out / "items.md").write_text(_items_md(meta, items), encoding="utf-8")
    sheet_path = out / "scoresheet.json"
    if not sheet_path.exists():  # nunca se pisa una hoja ya rellenada
        sheet_path.write_text(json.dumps(sheet, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "key.json").write_text(json.dumps({"seed": seed, "rep": rep, "key": key, "warning": "NO abrir hasta terminar la valoración"},
                                             ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "PROMPT.md").write_text(judge_prompt(run_dir), encoding="utf-8")
    return {"items": len(items), "dir": str(out), "labels": letters}


def _items_md(meta: dict[str, Any], items: list[dict[str, Any]]) -> str:
    L = [f"# Evaluación ciega · ejecución {meta['run_id']}", "",
         (f"{len(items)} conversaciones. Cada una trae la evidencia (verdad del snapshot congelado, idéntica para todas las respuestas) "
          "y las respuestas anonimizadas. La letra de cada modelo cambia de una conversación a otra."), ""]
    for it in items:
        L += [f"## {it['item']} · {it['category']}", ""]
        for t in it["turns"]:
            L += [f"**Turno {t['turn']} — pregunta:** {t['question']}", "",
                  "Evidencia: `" + json.dumps(t["evidence"], ensure_ascii=False) + "`", ""]
        for letter, turns in it["responses"].items():
            L.append(f"### Respuesta {letter}")
            for t in turns:
                L += [f"- Turno {t['turn']} (herramientas: {', '.join(t['tools']) or 'ninguna'}):", "", "  > " + t["answer"].replace("\n", "\n  > "), ""]
        L.append("")
    return "\n".join(L)


def judge_prompt(run_dir: Path) -> str:
    crit = "\n".join(f"{i}. **{c}** — {d}" for i, (c, d) in enumerate(CRITERIA, 1))
    rules = "\n".join(f"- `{k}`: {v}" for k, v in CLASS_RULES.items())
    return f"""# Prompt para Opus: evaluación ciega del benchmark de Futsito

Eres el juez de una comparación de modelos para Futsito, un asistente de fútbol sala que responde con datos oficiales de la RFFM
obtenidos por herramientas (MCP). Trabaja en dos fases y no las mezcles.

## Fase 1 — valoración ciega (no abras `blind/key.json`)

1. Lee `{run_dir}/blind/items.md` (o `items.json`). Cada conversación trae la **evidencia** (verdad del snapshot) y respuestas A, B, C…
2. Para cada respuesta, asigna una clase:
{rules}
3. Puntúa cada criterio con 0 (mal), 1 (a medias), 2 (bien) o null (no aplica), en este orden de prioridad:
{crit}
4. Reglas contra sesgos:
   - No premies la extensión ni el estilo elaborado: una respuesta corta y correcta vale más que una larga con un dato dudoso.
   - No penalices diferencias de redacción si el contenido es semánticamente correcto (números escritos en letra, orden distinto, sinónimos).
   - Ante falta de datos, lo correcto es abstenerse o pedir aclaración; inventar es `alucinacion` aunque suene plausible.
   - Juzga solo con la evidencia dada; si no basta para decidir, usa `no_evaluable` y dilo.
   - No intentes adivinar qué modelo es cada letra.
5. Escribe una justificación breve (una o dos frases) y, en `comparison`, solo las diferencias relevantes entre letras.
6. Guarda todo en `{run_dir}/blind/scoresheet.json` (respeta su estructura).

## Fase 2 — revelado y recomendación

7. Ejecuta: `python -m futsal.evals.bench reveal {run_dir}`. Une tu hoja con la clave, mide tu acuerdo con el calificador determinista
   y añade costes y latencias medidos (`blind/reveal.md`).
8. Con calidad y fiabilidad primero, y después coste y tiempo, recomienda el modelo con mejor relación calidad/precio. Señala:
   mejor modelo general, el más económico con calidad aceptable, el mejor para preguntas complejas y para sencillas, y si compensa un
   router híbrido. No recomiendes por diferencias pequeñas que los intervalos no sostienen; tus puntuaciones complementan, no
   sustituyen, los resultados deterministas de `report.md`. Donde discrepes del calificador, explica por qué con la evidencia.
"""


def reveal(run_dir: Path) -> dict[str, Any]:
    out = run_dir / BLIND_DIR
    key = json.loads((out / "key.json").read_text(encoding="utf-8"))["key"]
    sheet = json.loads((out / "scoresheet.json").read_text(encoding="utf-8"))
    pack = json.loads((out / "items.json").read_text(encoding="utf-8"))
    meta, results = load_run(run_dir)
    a = analyze(meta, results)
    rep = int(pack.get("rep", 0))
    det: dict[tuple[str, str], list[str]] = defaultdict(list)  # (item, modelo) -> clases deterministas de sus turnos
    item_case = {f"I{n:02d}": cid for n, cid in enumerate(meta["case_ids"], 1)}
    for m, rows in a["rows"].items():
        for r in rows:
            if r["rep"] == rep:
                for item, cid in item_case.items():
                    if cid == r["case_id"]:
                        det[(item, m)].append(r["class"])
    judged: dict[str, list[dict[str, Any]]] = defaultdict(list)
    missing = 0
    for entry in sheet["items"]:
        mapping = key.get(entry["item"], {})
        for letter, val in entry["labels"].items():
            if val.get("class") not in CLASS_RULES:
                missing += 1
                continue
            m = mapping[letter]
            classes = det.get((entry["item"], m), [])
            worst = max(classes, key=lambda c: CLASSES.index(c) if c in CLASSES else -1) if classes else None
            judged[m].append({"item": entry["item"], "class": val["class"], "scores": val.get("scores", {}), "deterministic": worst,
                              "justification": val.get("justification", "")})
    per_model: dict[str, Any] = {}
    for m, js in judged.items():
        graded = [j for j in js if j["deterministic"] in GRADED and j["class"] in GRADED]
        agree = sum(j["class"] == j["deterministic"] for j in graded)
        crit = {c: [j["scores"].get(c) for j in js if isinstance(j["scores"].get(c), int | float)] for c, _ in CRITERIA}
        per_model[m] = {"items": len(js), "classes": dict(Counter(j["class"] for j in js)),
                        "criteria_mean": {c: round(sum(v) / len(v), 3) if v else None for c, v in crit.items()},
                        "agreement_with_deterministic": {"k": agree, "n": len(graded)},
                        "disagreements": [j for j in graded if j["class"] != j["deterministic"]],
                        "cost": a["stats"][m]["cost"]["per_question"], "cost_per_correct": a["stats"][m]["cost"]["per_correct"],
                        "latency": a["stats"][m]["latency"], "deterministic_accuracy": a["stats"][m]["quality"]["accuracy"]}
    result = {"run_id": meta["run_id"], "unscored_labels": missing, "models": per_model}
    (out / "reveal.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    (out / "reveal.md").write_text(_reveal_md(result), encoding="utf-8")
    return result


def _reveal_md(r: dict[str, Any]) -> str:
    L = [f"# Revelado de la evaluación ciega · {r['run_id']}", ""]
    if r["unscored_labels"]:
        L += [f"**Aviso:** {r['unscored_labels']} respuestas sin clase válida en la hoja (no cuentan).", ""]
    names = [c for c, _ in CRITERIA]
    L += ["| modelo | clases del juez | " + " | ".join(names) + " | acuerdo con el calificador | exactitud determinista | coste medio/pregunta | coste por correcta | p50 |",
          "|---|---|" + "---|" * len(names) + "---|---|---|---|---|"]
    for m, s in sorted(r["models"].items()):
        ag = s["agreement_with_deterministic"]
        acc = s["deterministic_accuracy"]
        L.append(f"| {m} | {s['classes']} | " + " | ".join("—" if s["criteria_mean"][c] is None else f"{s['criteria_mean'][c]:.2f}" for c in names)
                 + f" | {ag['k']}/{ag['n']} | {acc['k']}/{acc['n']} | {s['cost']['mean']} | {s['cost_per_correct']} | {s['latency']['p50_ms']} ms |")
    L += ["", "## Discrepancias con el calificador determinista", ""]
    for m, s in sorted(r["models"].items()):
        for d in s["disagreements"]:
            L.append(f"- {m} · {d['item']}: juez `{d['class']}` frente a determinista `{d['deterministic']}` — {d['justification']}")
    L += ["", "Las puntuaciones del juez complementan los resultados deterministas de report.md; no los sustituyen.", ""]
    return "\n".join(L)
