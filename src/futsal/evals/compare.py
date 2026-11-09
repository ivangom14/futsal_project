"""Comparación A (referencia) frente a B (candidata) con la regla de decisión de docs/v2/evaluation.md §7.

1. 0 críticos nuevos. 2. Ningún estrato baja más que el umbral de ruido (la diferencia entre dos ejecuciones idénticas de la referencia;
sin ejecución de ruido, un umbral por defecto declarado). 3. Mejora de la tasa global (bootstrap pareado por turno, semilla fija, IC 95 %
por encima de 0) o igualdad con coste o latencia significativamente menores. Una mejora de coste o latencia con críticos nuevos se rechaza
siempre. Solo se comparan ejecuciones del MISMO snapshot.
"""

from collections import defaultdict
from typing import Any

from futsal.evals.report import GRADED
from futsal.evals.stats import bootstrap_ci, mean

DEFAULT_NOISE = 0.05
BETTER, COST_BETTER, NO_DIFF, REJECTED = "B_mejor", "B_igual_con_menor_coste_o_latencia", "sin_diferencia_demostrada", "B_rechazado"
EXIT_CODES = {BETTER: 0, COST_BETTER: 0, REJECTED: 1, NO_DIFF: 2}


class IncomparableRuns(ValueError):
    pass


def _table(results: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    """(caso, turno) -> exactitud media entre repeticiones, ¿algún crítico?, coste (o tokens) y latencia medios."""
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in results["records"]:
        if r["verdict"] in GRADED:
            groups[(r["case_id"], r["turn"])].append(r)
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for k, rs in groups.items():
        costs = [r["metrics"]["cost_usd"] for r in rs]
        out[k] = {"stratum": rs[0]["stratum"], "acc": sum(r["verdict"] == "correct" for r in rs) / len(rs),
                  "critical": any(r["verdict"] == "critical" for r in rs),
                  "cost": mean([c for c in costs if c is not None]) if all(c is not None for c in costs) else None,
                  "tokens": mean([r["metrics"]["input_tokens"] + r["metrics"]["billed_output_tokens"] for r in rs]),
                  "latency": mean([r["timing"]["latency_ms"] for r in rs])}
    return out


def _acc(rows: list[dict[str, Any]]) -> float | None:
    return mean([r["acc"] for r in rows])


def _stratum_acc(table: dict[Any, dict[str, Any]], keys: list[Any]) -> dict[str, tuple[float, int]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for k in keys:
        by[table[k]["stratum"]].append(table[k])
    return {s: (_acc(rows) or 0.0, len(rows)) for s, rows in by.items()}


def noise_thresholds(ref: dict[str, Any], ref2: dict[str, Any] | None) -> dict[str, float]:
    """Umbral de ruido por estrato: |exactitud(ref) − exactitud(ref repetida)|, con la resolución de un turno como mínimo."""
    t1 = _table(ref)
    if ref2 is None:
        strata = {v["stratum"] for v in t1.values()}
        return {s: DEFAULT_NOISE for s in strata}
    t2 = _table(ref2)
    keys = [k for k in t1 if k in t2]
    a, b = _stratum_acc(t1, keys), _stratum_acc(t2, keys)
    return {s: max(abs(a[s][0] - b[s][0]), 1 / a[s][1]) for s in a}


def _significant_drop(diffs: list[float]) -> bool:
    ci = bootstrap_ci(diffs)
    return ci is not None and ci[1] < 0


def compare(a: dict[str, Any], b: dict[str, Any], noise_run: dict[str, Any] | None = None) -> dict[str, Any]:
    fa, fb = a["meta"]["snapshot"]["fingerprint"], b["meta"]["snapshot"]["fingerprint"]
    if fa != fb:
        raise IncomparableRuns(f"snapshots distintos ({fa[:12]} ≠ {fb[:12]}): las comparaciones solo valen dentro del mismo snapshot")
    if a["meta"]["suite"] != b["meta"]["suite"]:
        raise IncomparableRuns(f"suites distintas ({a['meta']['suite']} ≠ {b['meta']['suite']})")
    ta, tb = _table(a), _table(b)
    keys = sorted(k for k in ta if k in tb)
    reasons: list[str] = []
    new_critical = sorted(f"{k[0]}#{k[1] + 1}" for k in keys if tb[k]["critical"] and not ta[k]["critical"])
    fixed_critical = sorted(f"{k[0]}#{k[1] + 1}" for k in keys if ta[k]["critical"] and not tb[k]["critical"])
    thresholds = noise_thresholds(a, noise_run)
    sa, sb = _stratum_acc(ta, keys), _stratum_acc(tb, keys)
    strata: dict[str, Any] = {}
    worse: list[str] = []
    for s in sorted(sa):
        drop = sa[s][0] - sb[s][0]
        limit = thresholds.get(s, DEFAULT_NOISE)
        strata[s] = {"turns": sa[s][1], "acc_a": round(sa[s][0], 4), "acc_b": round(sb[s][0], 4), "delta": round(-drop, 4),
                     "noise_threshold": round(limit, 4)}
        if drop > limit:
            worse.append(f"{s}: baja {drop * 100:.1f} pts (umbral de ruido {limit * 100:.1f})")
    acc_diffs = [tb[k]["acc"] - ta[k]["acc"] for k in keys]
    ci = bootstrap_ci(acc_diffs)
    delta = mean(acc_diffs)
    metric = "cost" if all(ta[k]["cost"] is not None and tb[k]["cost"] is not None for k in keys) and keys else "tokens"
    cost_diffs = [tb[k][metric] - ta[k][metric] for k in keys]
    lat_diffs = [tb[k]["latency"] - ta[k]["latency"] for k in keys]
    cheaper, faster = bool(keys) and _significant_drop(cost_diffs), bool(keys) and _significant_drop(lat_diffs)
    if new_critical:
        decision = REJECTED
        reasons.append(f"{len(new_critical)} crítico(s) nuevo(s): {', '.join(new_critical[:8])}"
                       + (" (una mejora de coste o latencia no lo compensa)" if cheaper or faster else ""))
    elif worse:
        decision = REJECTED
        reasons += [f"estrato peor que el umbral de ruido → {w}" for w in worse]
    elif ci is not None and ci[1] < 0:
        decision = REJECTED
        reasons.append(f"la exactitud global baja de forma significativa (IC95 [{ci[0]:+.3f}, {ci[1]:+.3f}])")
    elif ci is not None and ci[0] > 0:
        decision = BETTER
        reasons.append(f"mejora global de la exactitud: {delta:+.3f} (IC95 [{ci[0]:+.3f}, {ci[1]:+.3f}])")
    elif cheaper or faster:
        decision = COST_BETTER
        reasons.append("exactitud sin diferencia demostrada y " + " y ".join(
            x for x, ok in (("coste significativamente menor", cheaper), ("latencia significativamente menor", faster)) if ok))
    else:
        decision = NO_DIFF
        reasons.append("ninguna diferencia significativa en exactitud, coste ni latencia")
    return {"decision": decision, "exit_code": EXIT_CODES[decision], "reasons": reasons, "turns_compared": len(keys),
            "accuracy_delta": delta, "accuracy_ci95": list(ci) if ci else None, "new_critical": new_critical,
            "fixed_critical": fixed_critical, "strata": strata, "cost_metric": metric,
            "cost_delta_mean": mean(cost_diffs), "latency_delta_mean_ms": mean(lat_diffs), "cheaper": cheaper, "faster": faster,
            "noise": "medido con una segunda ejecución de la referencia" if noise_run is not None
            else f"sin ejecución de ruido: umbral por defecto {DEFAULT_NOISE * 100:.0f} pts",
            "bootstrap": "pareado por turno, semilla fija"}


def render(result: dict[str, Any]) -> str:
    lines = [f"Decisión: {result['decision']}", *[f"  - {r}" for r in result["reasons"]],
             f"Turnos comparados: {result['turns_compared']} · Δ exactitud {result['accuracy_delta']} · IC95 {result['accuracy_ci95']}",
             f"Críticos nuevos: {result['new_critical'] or 'ninguno'} · corregidos: {result['fixed_critical'] or 'ninguno'}",
             f"Ruido: {result['noise']}", "Estrato | turnos | A | B | Δ | umbral"]
    lines += [f"{s} | {v['turns']} | {v['acc_a']:.3f} | {v['acc_b']:.3f} | {v['delta']:+.3f} | {v['noise_threshold']:.3f}"
              for s, v in result["strata"].items()]
    return "\n".join(lines)
