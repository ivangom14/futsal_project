"""Comparación CONTROLADA del prompt `legacy` frente al `compact` con repeticiones (Fase 1). Consume cuota del LLM solo con --yes.

    python scripts/ab_prompt_modes.py            # solo imprime los comandos y el coste estimado en llamadas (no llama a nada)
    python scripts/ab_prompt_modes.py --plan     # pide al evaluador su plan por modo (no llama al LLM; necesita API y base de datos)
    python scripts/ab_prompt_modes.py --yes      # ejecuta las 6 tandas (3 repeticiones x 2 modos) y resume al final

Casos: los 6 que empeoraron y los 10 que mejoraron en la primera comparación. Mismo modelo, suite y condiciones en todas las tandas; solo cambia
AGENT_PROMPT_MODE (lo leen el agente y el servidor MCP). Las tandas se alternan (L C · C L · L C) para que la hora del día o la carga de Gemini no
favorezcan siempre al mismo modo. Cada tanda deja su carpeta (results.json, report.md, trazas crudas y saneadas) y su copia `<modo>-r<n>.json`;
si una tanda ya existe se omite (reanuda), salvo --force. Solo biblioteca estándar. No lee ni imprime claves.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REGRESSED = ["acta_jugador_inexistente", "circular27_comunica", "circular10_anula", "circular10_saque_meta_coloquial",
             "tabla_goleadores_nombre_repetido", "tabla_goleadores_por_jornada_no_verificable"]
IMPROVED = ["equipo_proximo_partido", "acta_goleadores_ultimo", "acta_arbitro", "acta_jornada_sin_resultados", "acta_tarjetas_codigo",
            "acta_sustituciones_minuto", "regla_duracion_mitad", "circular27_fechas_rfef_fifa", "circular10_penal_no_acumulable",
            "combinada_lider_ultimo_resultado"]
CASES = REGRESSED + IMPROVED
MODES = ("legacy", "compact")
CALLS_PER_CASE = (2, 3.2, 5)  # mínimo, típico y alto de llamadas al LLM por caso (ejecución live de la Fase 17: 2,4 de media; estos casos tienen más herramientas)


def order(rep: int) -> tuple[str, str]:
    """Alterna qué modo va primero en cada repetición."""
    return MODES if rep % 2 == 1 else MODES[::-1]


def command(mode: str, rep: int, out: Path, max_requests: int, min_interval: float | None, plan: bool = False) -> list[str]:
    cmd = [sys.executable, "-m", "futsal.agent.evaluate", "--mode", "live", "--only", ",".join(CASES), "--max-requests", str(max_requests)]
    if plan:
        return cmd + ["--plan"]
    cmd += ["--confirm-live", "--report-dir", str(out / f"{mode}-r{rep}"), "--output", str(out / f"{mode}-r{rep}.json")]
    if min_interval is not None:
        cmd += ["--min-interval", str(min_interval)]
    return cmd


def runs(reps: int) -> list[tuple[str, int]]:
    return [(mode, rep) for rep in range(1, reps + 1) for mode in order(rep)]


def estimate(reps: int, max_requests: int) -> dict[str, int]:
    n = len(CASES) * reps * len(MODES)
    lo, typical, hi = (round(n * c) for c in CALLS_PER_CASE)
    return {"case_runs": n, "llm_low": lo, "llm_typical": typical, "llm_high": hi, "hard_cap": reps * len(MODES) * max_requests}


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="ejecuta de verdad (llamadas reales y de pago al LLM)")
    ap.add_argument("--plan", action="store_true", help="solo el plan del evaluador por modo (sin LLM)")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", default="data/agent/ab")
    ap.add_argument("--max-requests", type=int, default=100, help="tope DURO de peticiones al LLM por tanda (llamadas + reintentos)")
    ap.add_argument("--min-interval", type=float, default=None, help="pausa entre peticiones (por defecto la del evaluador: 4 s)")
    ap.add_argument("--force", action="store_true", help="repite las tandas que ya tienen resultado")
    args = ap.parse_args(argv)
    out = Path(args.out)
    est = estimate(args.reps, args.max_requests)
    print(f"Casos ({len(CASES)}): {len(REGRESSED)} que empeoraron + {len(IMPROVED)} que mejoraron\n  " + ", ".join(CASES))
    print(f"Tandas: {len(MODES) * args.reps} ({args.reps} repeticiones x {len(MODES)} modos) · {est['case_runs']} ejecuciones de caso")
    print(f"Llamadas al LLM estimadas: ~{est['llm_typical']} (rango {est['llm_low']}-{est['llm_high']}); tope duro {est['hard_cap']} "
          f"({args.max_requests} por tanda). Tiempo orientativo con la pausa de 4 s: ~{round(est['llm_typical'] * 5.2 / 60)} min.")
    print("Orden de las tandas (se alterna para no favorecer a un modo): " + ", ".join(f"{m} r{r}" for m, r in runs(args.reps)))
    template = " ".join(command("<modo>", 0, out, args.max_requests, args.min_interval)[2:]).replace("<modo>-r0", "<modo>-r<n>")
    print(f"Cada tanda, con AGENT_PROMPT_MODE=<modo> en el entorno:\n  python -m {template}")
    if args.plan:
        for mode in MODES:
            print(f"\n== Plan del evaluador con AGENT_PROMPT_MODE={mode} (no llama al LLM) ==", flush=True)
            subprocess.run(command(mode, 1, out, args.max_requests, args.min_interval, plan=True), env={**os.environ, "AGENT_PROMPT_MODE": mode}, check=False)
        return 0
    if not args.yes:
        print("\nNo se ha llamado a ningún LLM. Añade --yes para ejecutar (consume cuota) o --plan para ver el plan del evaluador.")
        return 0
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"started": datetime.now(UTC).isoformat(), "commit": git("rev-parse", "--short", "HEAD"), "dirty": bool(git("status", "--porcelain")),
                "cases": CASES, "max_requests": args.max_requests, "runs": []}
    code = 0
    for mode, rep in runs(args.reps):
        target = out / f"{mode}-r{rep}.json"
        if target.exists() and not args.force:
            print(f"\n== {mode} r{rep}: ya existe {target}, se omite ==")
            manifest["runs"].append({"mode": mode, "rep": rep, "skipped": True})
            continue
        print(f"\n== {mode} r{rep} ==", flush=True)
        res = subprocess.run(command(mode, rep, out, args.max_requests, args.min_interval), env={**os.environ, "AGENT_PROMPT_MODE": mode}, check=False)
        manifest["runs"].append({"mode": mode, "rep": rep, "exit": res.returncode, "finished": datetime.now(UTC).isoformat()})
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        if res.returncode not in (0, 1):  # 1 = hay fallos de casos (esperable); otro código = no se pudo ejecutar
            print(f"La tanda {mode} r{rep} terminó con código {res.returncode}: se detiene para no gastar cuota.")
            return res.returncode
        code = max(code, 0)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print("\n== Resumen ==", flush=True)
    subprocess.run([sys.executable, "-m", "futsal.agent.ab_summary", str(out), "--json", str(out / "summary.json"), "--md", str(out / "summary.md")], check=False)
    print(f"\nResumen: {out / 'summary.md'} · detalle: {out / 'summary.json'} · trazas saneadas: carpetas {out}\\<modo>-r<n>\\*\\traces.sanitized.jsonl")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
