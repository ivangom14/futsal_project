"""Comparación CONTROLADA entre N brazos (combinaciones de banderas AGENT_*) con repeticiones (Fase E0). Consume cuota del LLM solo con --yes.

    python scripts/ab_arms.py --arms legacy,prefetch --cases prefetch_probe            # solo el plan: tandas, ejecuciones y llamadas (no llama a nada)
    python scripts/ab_arms.py --arms legacy,prefetch --cases prefetch_probe --plan-evaluator   # además, el plan del propio evaluador (sin LLM; API y BD)
    python scripts/ab_arms.py --arms legacy,prefetch --cases prefetch_probe --yes      # ejecuta (consume cuota)

Cada tanda fija TODAS las banderas del brazo (ver `futsal.agent.arms`) y apaga la telemetría; el resto de condiciones (modelo, casos, tope de peticiones, pausa)
es idéntico. Las tandas rotan el orden de los brazos entre repeticiones. Cada una deja su carpeta y su copia `<brazo>-r<n>.json`; si ya existe se omite
(reanuda) salvo --force. NO sustituye a `scripts/ab_prompt_modes.py` (legacy frente a compact), que sigue intacto.
Presupuesto GLOBAL REAL: `--max-total-requests` (por defecto, tandas × --max-requests) limita las peticiones al LLM (llamadas + reintentos) de TODA la ejecución
(todos los brazos, tandas y `--fill-missing`). Cada tanda recibe min(--max-requests, lo que queda) como tope DURO que aplica el propio cliente del evaluador
(`BudgetedLLM`: no envía ninguna petición que pudiera superarlo); al agotarse el global no se lanza ninguna tanda más.
Solo biblioteca estándar más `futsal.agent.arms`. No lee ni imprime claves.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from futsal.agent.arms import ARMS, PRESETS, QUOTA_EXIT_CODE, arm_env

MEASURED_INPUT_PER_CALL = 4626  # MEDIDO: 744.865 tokens de entrada / 161 llamadas en tu comparación live legacy (63 casos comunes)
MEASURED_OUTPUT_PER_CALL = 49   # MEDIDO: 2.040 tokens de salida / 42 llamadas en tu ejecución live legacy de 13 casos
ESTIMATED_CALLS_PER_CASE = 3.2  # ESTIMADO: casos con herramientas (la suite completa promedia 2,56)
DESIGN_BOUND_PER_CASE = 7       # cota teórica de diseño: hasta 5 herramientas + respuesta final + una reconsulta de guardarraíl
SECONDS_PER_REQUEST = 5.2       # ESTIMADO: ~1,2 s de latencia + 4 s de pausa de ritmo


def split_arms(text: str) -> list[str]:
    arms = [a.strip() for a in text.split(",") if a.strip()]
    if len(arms) < 2 or len(set(arms)) != len(arms):
        raise SystemExit("--arms necesita al menos dos brazos distintos")
    for a in arms:
        arm_env(a)  # valida (lanza ValueError con la lista de brazos)
    return arms


def case_selection(spec: str) -> tuple[list[str], list[str]]:
    """(ids para --only, argumentos de selección). `suite:all` / `suite:smoke` usan --suite."""
    if spec.startswith("suite:"):
        return [], ["--suite", spec.split(":", 1)[1]]
    ids = PRESETS.get(spec) or [c.strip() for c in spec.split(",") if c.strip()]
    return ids, ["--only", ",".join(ids)]


def schedule(arms: list[str], reps: int) -> list[tuple[str, int]]:
    """Rota el orden de los brazos en cada repetición (cuadrado latino simple)."""
    out = []
    for rep in range(1, reps + 1):
        k = (rep - 1) % len(arms)
        out += [(a, rep) for a in arms[k:] + arms[:k]]
    return out


def command(arm: str, rep: int, out: Path, selection: list[str], max_requests: int, min_interval: float | None, plan: bool = False) -> list[str]:
    cmd = [sys.executable, "-m", "futsal.agent.evaluate", "--mode", "live", *selection, "--max-requests", str(max_requests)]
    if plan:
        return cmd + ["--plan"]
    cmd += ["--confirm-live", "--report-dir", str(out / f"{arm}-r{rep}"), "--output", str(out / f"{arm}-r{rep}.json")]
    if min_interval is not None:
        cmd += ["--min-interval", str(min_interval)]
    return cmd


def pending_cases(doc: dict) -> list[str]:
    return [c["case_id"] for c in doc.get("cases", []) if c.get("status") == "not_run"]


BUDGET_TEXT = "presupuesto de peticiones agotado"


@dataclass
class Budget:
    """Presupuesto GLOBAL de peticiones al LLM de una ejecución de este script (llamadas + reintentos)."""

    total: int
    per_batch: int
    used: int = 0

    def remaining(self) -> int:
        return max(self.total - self.used, 0)

    def grant(self) -> int:
        """Tope duro de la próxima tanda: nunca más de lo que queda."""
        return min(self.per_batch, self.remaining())

    def charge(self, granted: int, actual: int | None) -> int:
        """Suma lo consumido; si no se puede saber (la tanda no dejó resultados), se cuenta TODO lo concedido."""
        spent = granted if actual is None else actual
        self.used += spent
        return spent


def requests_used(path: Path) -> int | None:
    """Peticiones al LLM de una tanda a partir de su results.json (llamadas + reintentos). None si no hay resultados legibles.
    Un caso rechazado por el tope duro no envió nada: su registro de llamada no cuenta."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        total = 0
        for c in doc.get("cases", []):
            n = int(c.get("llm_calls") or 0) + int(c.get("llm_retries") or 0)
            if BUDGET_TEXT in str(c.get("detail") or "") and n:
                n -= 1
            total += n
        return total
    except (OSError, ValueError, TypeError):
        return None


def quota_hint(path: Path) -> str:
    """Cuándo vuelve la cuota, según lo que pidió el servidor en los casos afectados (el mayor tiempo de espera pedido)."""
    try:
        text = json.dumps(json.loads(path.read_text(encoding="utf-8")).get("cases", []), ensure_ascii=False)
        waits = [int(m) for m in re.findall(r"esperar (\d+)s", text)]
    except (OSError, ValueError):
        waits = []
    if not waits:
        return "el servidor no indicó cuándo se restablece"
    when = datetime.now(UTC) + timedelta(seconds=max(waits))
    return f"el servidor pide esperar {max(waits)} s (≈{max(waits) / 3600:.1f} h): se restablece hacia las {when:%Y-%m-%d %H:%M} UTC"


def merge_fill(orig: dict, fill: dict) -> dict:
    """Sustituye en `orig` los casos `not_run` por los de `fill` que ya se han ejecutado (pass/fail/no evaluable); marca `filled`."""
    new = {c["case_id"]: c for c in fill.get("cases", []) if c.get("status") != "not_run"}
    orig["cases"] = [{**new[c["case_id"]], "filled": True} if c.get("status") == "not_run" and c["case_id"] in new else c for c in orig.get("cases", [])]
    orig.setdefault("meta", {})["filled_cases"] = sorted(new)
    return orig


def fill_missing(arms: list[str], reps: int, out: Path, min_interval: float | None, budget: Budget) -> int:
    """Repite SOLO los casos `not_run` (429, red, presupuesto) de cada <brazo>-r<n>.json con las banderas de su brazo y los funde en el original (queda .orig.json).
    Consume el MISMO presupuesto global que las tandas normales."""
    for arm, rep in schedule(arms, reps):
        target = out / f"{arm}-r{rep}.json"
        if not target.exists():
            continue
        doc = json.loads(target.read_text(encoding="utf-8"))
        ids = pending_cases(doc)
        if not ids:
            continue
        cap = budget.grant()
        if cap <= 0:
            print(f"\nPresupuesto global agotado ({budget.used}/{budget.total}): no se completa {arm} r{rep} ni lo que falte.")
            return 3
        print(f"\n== {arm} r{rep}: completando {len(ids)} caso(s) sin ejecutar: {', '.join(ids)} (tope duro de esta tanda: {cap}) ==", flush=True)
        part = out / f"{arm}-r{rep}-fill.json"
        cmd = command(arm, rep, out, ["--only", ",".join(ids)], cap, min_interval)
        cmd[cmd.index("--report-dir") + 1] = str(out / f"{arm}-r{rep}-fill")
        cmd[cmd.index("--output") + 1] = str(part)
        res = subprocess.run(cmd, env={**os.environ, **arm_env(arm)}, check=False)
        spent = budget.charge(cap, requests_used(part) if res.returncode in (0, 1, QUOTA_EXIT_CODE) else None)
        print(f"   peticiones usadas: {spent} · global {budget.used}/{budget.total}", flush=True)
        if res.returncode not in (0, 1, QUOTA_EXIT_CODE):
            print(f"La tanda de completado {arm} r{rep} terminó con código {res.returncode}: se detiene.")
            return res.returncode
        hint = quota_hint(part) if res.returncode == QUOTA_EXIT_CODE else ""
        if part.exists():  # también con la cuota agotada: lo que sí se completó se conserva
            (out / f"{arm}-r{rep}.orig.json").write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
            target.write_text(json.dumps(merge_fill(doc, json.loads(part.read_text(encoding="utf-8"))), ensure_ascii=False, indent=1), encoding="utf-8")
            part.unlink()
        if res.returncode == QUOTA_EXIT_CODE:
            print(f"\nCUOTA DEL PROVEEDOR AGOTADA: se detiene el relleno sin gastar más peticiones; {hint}.")
            return QUOTA_EXIT_CODE
    return 0


def plan_numbers(n_arms: int, reps: int, n_cases: int | None, max_requests: int, input_per_call: float, output_per_call: float,
                 price_in: float | None, price_out: float | None) -> dict[str, object]:
    batches = n_arms * reps
    out: dict[str, object] = {"batches": batches, "hard_cap_requests": batches * max_requests}  # `hard_cap_requests` = cota por defecto del global (tandas × tope por tanda)
    if n_cases is None:  # suite entera: el nº de casos lo sabe el evaluador (--plan-evaluator)
        out["case_runs"] = None
        return out
    runs = batches * n_cases
    typical = round(runs * ESTIMATED_CALLS_PER_CASE)
    out.update(calls_per_batch=round(n_cases * ESTIMATED_CALLS_PER_CASE), calls_per_arm=round(reps * n_cases * ESTIMATED_CALLS_PER_CASE))
    out.update(case_runs=runs, calls_typical=typical, calls_design_bound=runs * DESIGN_BOUND_PER_CASE,
               input_tokens=round(typical * input_per_call), output_tokens=round(typical * output_per_call),
               minutes=round(typical * SECONDS_PER_REQUEST / 60))
    if price_in is not None and price_out is not None:
        out["cost_usd"] = round(typical * input_per_call * price_in / 1e6 + typical * output_per_call * price_out / 1e6, 4)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arms", required=True, help=f"brazos separados por comas; disponibles: {', '.join(ARMS)}")
    ap.add_argument("--cases", default="prefetch_probe", help=f"preset ({', '.join(PRESETS)}), suite:all, suite:smoke o ids separados por comas")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", default="data/agent/ab-arms")
    ap.add_argument("--max-requests", type=int, default=100, help="tope DURO de peticiones al LLM POR TANDA (llamadas + reintentos); la tanda recibe min(esto, lo que quede del global)")
    ap.add_argument("--max-total-requests", type=int, default=None,
                    help="tope DURO GLOBAL de peticiones al LLM de TODA la ejecución (brazos, tandas, reintentos y --fill-missing). Por defecto: tandas × --max-requests")
    ap.add_argument("--min-interval", type=float, default=None)
    ap.add_argument("--input-per-call", type=float, default=MEASURED_INPUT_PER_CALL, help="tokens de entrada por llamada para la estimación")
    ap.add_argument("--output-per-call", type=float, default=MEASURED_OUTPUT_PER_CALL)
    ap.add_argument("--price-in", type=float, default=None, help="USD por millón de tokens de entrada (tarifa VERIFICADA, aportada por ti)")
    ap.add_argument("--price-out", type=float, default=None)
    ap.add_argument("--price-source", default=None, help="de dónde sale la tarifa (obligatorio con --price-in/--price-out)")
    ap.add_argument("--price-date", default=None, help="fecha de verificación de la tarifa (obligatorio con --price-in/--price-out)")
    ap.add_argument("--plan-evaluator", action="store_true", help="pide el plan al evaluador (sin LLM; necesita API y base de datos)")
    ap.add_argument("--yes", action="store_true", help="ejecuta de verdad (llamadas reales y de pago al LLM)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--fill-missing", action="store_true", help="con --yes: repite solo los casos not_run (429, red) de las tandas existentes y los funde en ellas")
    args = ap.parse_args(argv)
    arms = split_arms(args.arms)
    ids, selection = case_selection(args.cases)
    priced = args.price_in is not None and args.price_out is not None
    if (args.price_in is not None) != (args.price_out is not None) or (priced and not (args.price_source and args.price_date)):
        raise SystemExit("las tarifas exigen --price-in, --price-out, --price-source y --price-date a la vez: sin fuente y fecha no es una tarifa verificada")
    out = Path(args.out)
    n = plan_numbers(len(arms), args.reps, len(ids) if ids else None, args.max_requests, args.input_per_call, args.output_per_call,
                     args.price_in if priced else None, args.price_out if priced else None)
    print(f"Brazos: {', '.join(arms)} · casos: {len(ids) if ids else args.cases} ({args.cases}) · repeticiones: {args.reps}")
    for a in arms:
        print(f"  {a}: " + " ".join(f"{k}={v}" for k, v in arm_env(a).items()))
    print(f"Tandas: {n['batches']} · ejecuciones de caso (máximo global): {n['case_runs'] if n['case_runs'] is not None else 'las fija la suite (usa --plan-evaluator)'}")
    total_cap = args.max_total_requests if args.max_total_requests is not None else int(n["hard_cap_requests"])  # type: ignore[call-overload]
    if total_cap <= 0 or args.max_requests <= 0:
        raise SystemExit("--max-requests y --max-total-requests deben ser positivos")
    pending = [(a, r) for a, r in schedule(arms, args.reps) if not (out / f"{a}-r{r}.json").exists() or args.force]
    print(f"PRESUPUESTO GLOBAL MÁXIMO (peticiones al LLM incl. reintentos; SE APLICA: cada tanda recibe min({args.max_requests}, lo que quede) como tope duro y no se lanza ninguna "
          f"tanda cuando se agota): {total_cap}")
    print(f"Tandas totales: {n['batches']} · pendientes de ejecutar: {len(pending)} · tope duro por tanda: {args.max_requests} · cota por defecto del global: {n['hard_cap_requests']}")
    if "calls_typical" in n:
        print(f"Consumo ESTIMADO (a {ESTIMATED_CALLS_PER_CASE} llamadas por caso; es una estimación, no un tope): por tanda ≈ {n['calls_per_batch']} · por brazo ≈ {n['calls_per_arm']} · "
              f"total ≈ {n['calls_typical']}" + ("  (¡supera el presupuesto global: la ejecución se detendrá al agotarlo!)" if int(n["calls_typical"]) > total_cap else ""))  # type: ignore[call-overload]
    if "calls_typical" in n:
        print(f"Llamadas LLM ESTIMADAS: ~{n['calls_typical']} (a {ESTIMATED_CALLS_PER_CASE} por caso) · cota teórica de diseño (no estimada): {n['calls_design_bound']}")
        print(f"Tokens ESTIMADOS: entrada ≈ {n['input_tokens']:,} (a {args.input_per_call:g} por llamada, MEDIDO en tu ejecución anterior) · salida ≈ {n['output_tokens']:,} "
              f"(a {args.output_per_call:g} por llamada, MEDIDO) · tiempo orientativo ~{n['minutes']} min")
    print("Coste: " + (f"≈ {n['cost_usd']} USD (ESTIMACIÓN con la tarifa que aportas: {args.price_source}, verificada el {args.price_date})" if "cost_usd" in n
                       else "n/d — sin tarifa verificada (aporta --price-in, --price-out, --price-source y --price-date)"))
    print("Orden de las tandas: " + ", ".join(f"{a} r{r}" for a, r in schedule(arms, args.reps)))
    print("Cada tanda, con las banderas del brazo en el entorno:\n  python -m " + " ".join(command("<brazo>", 0, out, selection, args.max_requests, args.min_interval)[2:]).replace("<brazo>-r0", "<brazo>-r<n>"))
    if args.plan_evaluator:
        for a in arms[:1]:
            print(f"\n== Plan del evaluador (brazo {a}; no llama al LLM) ==", flush=True)
            subprocess.run(command(a, 1, out, selection, args.max_requests, args.min_interval, plan=True), env={**os.environ, **arm_env(a)}, check=False)
        return 0
    if not args.yes:
        print("\nNo se ha llamado a ningún LLM. Añade --yes para ejecutar (consume cuota).")
        return 0
    out.mkdir(parents=True, exist_ok=True)
    budget = Budget(total_cap, args.max_requests)
    if args.fill_missing:
        todo = {f"{a}-r{r}": pending_cases(json.loads((out / f"{a}-r{r}.json").read_text(encoding="utf-8"))) for a, r in schedule(arms, args.reps) if (out / f"{a}-r{r}.json").exists()}
        print("Casos sin ejecutar por tanda (--fill-missing): " + (", ".join(f"{k}: {len(v)}" for k, v in todo.items() if v) or "ninguno"))
        code = fill_missing(arms, args.reps, out, args.min_interval, budget)
        print(f"\nPeticiones al LLM de esta ejecución: {budget.used}/{budget.total}")
        if code in (0, 3, QUOTA_EXIT_CODE):
            subprocess.run([sys.executable, "-m", "futsal.agent.arms_summary", str(out), "--json", str(out / "summary.json"), "--md", str(out / "summary.md")], check=False)
        return code
    manifest: dict = {"started": datetime.now(UTC).isoformat(), "arms": {a: arm_env(a) for a in arms}, "cases": ids or args.cases, "max_requests": args.max_requests,
                      "max_total_requests": total_cap, "runs": []}
    for arm, rep in schedule(arms, args.reps):
        target = out / f"{arm}-r{rep}.json"
        if target.exists() and not args.force:
            print(f"\n== {arm} r{rep}: ya existe {target}, se omite ==")
            manifest["runs"].append({"arm": arm, "rep": rep, "skipped": True})
            continue
        cap = budget.grant()
        if cap <= 0:
            manifest["budget"] = {"total": total_cap, "used": budget.used, "exhausted": True}
            (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
            print(f"\nPresupuesto global agotado ({budget.used}/{total_cap}): no se lanza {arm} r{rep} ni ninguna tanda más.")
            subprocess.run([sys.executable, "-m", "futsal.agent.arms_summary", str(out), "--json", str(out / "summary.json"), "--md", str(out / "summary.md")], check=False)
            return 3
        print(f"\n== {arm} r{rep} (tope duro de la tanda: {cap}; global {budget.used}/{total_cap}) ==", flush=True)
        res = subprocess.run(command(arm, rep, out, selection, cap, args.min_interval), env={**os.environ, **arm_env(arm)}, check=False)
        spent = budget.charge(cap, requests_used(target) if res.returncode in (0, 1, QUOTA_EXIT_CODE) else None)
        manifest["runs"].append({"arm": arm, "rep": rep, "exit": res.returncode, "finished": datetime.now(UTC).isoformat(), "max_requests_granted": cap, "requests_used": spent})
        manifest["budget"] = {"total": total_cap, "used": budget.used, "exhausted": False}
        if res.returncode == QUOTA_EXIT_CODE:
            manifest["quota_exhausted"] = {"at": f"{arm} r{rep}", "hint": quota_hint(target)}
            (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
            print(f"\nCUOTA DEL PROVEEDOR AGOTADA en {arm} r{rep}: no se lanza ninguna tanda más; {manifest['quota_exhausted']['hint']}.")
            print(f"Peticiones al LLM de esta ejecución: {budget.used}/{total_cap}")
            subprocess.run([sys.executable, "-m", "futsal.agent.arms_summary", str(out), "--json", str(out / "summary.json"), "--md", str(out / "summary.md")], check=False)
            print("Cuando se restablezca, completa lo pendiente con --fill-missing (no hagas git pull antes: todas las repeticiones deben salir del mismo código).")
            return QUOTA_EXIT_CODE
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        if res.returncode not in (0, 1):  # 1 = hay casos fallidos (esperable); otro código = no se pudo ejecutar
            print(f"La tanda {arm} r{rep} terminó con código {res.returncode}: se detiene para no gastar cuota.")
            return res.returncode
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"\nPeticiones al LLM de esta ejecución: {budget.used}/{total_cap}")
    print("\n== Resumen ==", flush=True)
    subprocess.run([sys.executable, "-m", "futsal.agent.arms_summary", str(out), "--json", str(out / "summary.json"), "--md", str(out / "summary.md")], check=False)
    print(f"\nResumen: {out / 'summary.md'} · detalle: {out / 'summary.json'} · trazas saneadas: {out}\\<brazo>-r<n>\\*\\traces.sanitized.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
