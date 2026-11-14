"""Ejecución del benchmark multimodelo: el agente Futsito completo (mismas herramientas MCP, mismas reglas, mismo snapshot congelado y
mismo reloj) con cada modelo del registro, reutilizando `futsal.evals.runner.run_eval` sin cambios de conducta.

Equidad: una sola BD de snapshot para todos; mismo brazo (banderas) del agente; caché de prompts de Anthropic desactivada; orden de
los modelos barajado con semilla (se guarda); repeticiones en orden rotado de casos (lo hace `run_eval`). Sin concurrencia: un modelo
tras otro, con la separación mínima entre peticiones de cada uno (`limits.min_interval_s`).

Gasto: presupuesto global en USD (`CostGuard`, compartido por todos los modelos) además del tope duro de peticiones de cada modelo.
Al agotarse, lo que falte queda `not_run` y se completa con `--resume` (sin repetir lo hecho). Un modelo que falla no invalida a los
demás: su estado queda en `bench.json`.
"""

import json
import os
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from futsal.agent.agent import ARCHS, CONTEXT_MODES, resolve_context_mode
from futsal.agent.llm import (
    BUDGET_EXHAUSTED,
    LLMClient,
    LLMError,
    LLMResponse,
    Message,
    ToolCall,
    Usage,
)
from futsal.evals.bench.registry import ModelSpec, build_llm, call_cost
from futsal.evals.datasets import bench_suite_file
from futsal.evals.runner import RESULTS, RunConfig, commit_id, prepare_database, run_eval
from futsal.evals.snapshot import MADRID

BENCH_FILE = "bench.json"
DEFAULT_ROOT = Path("data/bench")
DEFAULT_SEED = 20261114
DRY_RUN_TEXT = "No dispongo de esa información en mis herramientas (ejecución de prueba, sin modelo real)."


class BenchError(RuntimeError):
    """El benchmark no puede empezar (configuración, presupuesto o directorio)."""


# ----------------------------------------------------------------------------- suite
def load_suite(name: str, root: Path = Path(".")) -> dict[str, Any]:
    path = bench_suite_file(name, root)
    if path is None:
        raise BenchError(f"suite de benchmark desconocida: {name} (ficheros en evals/bench/suites/)")
    doc: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return doc


def suite_case_ids(suite: dict[str, Any], limit: int | None) -> list[str]:
    """Casos en el orden del fichero (intercalado por categoría): con `limit`, los N primeros cubren todas las categorías posibles."""
    ids = [c["id"] for c in suite["cases"]]
    if limit is not None:
        if limit < 1:
            raise BenchError("--limit debe ser ≥ 1")
        ids = ids[:limit]
    return ids


# ----------------------------------------------------------------------------- presupuesto en USD
@dataclass
class CostGuard:
    """Presupuesto global en USD. Antes de cada llamada exige que el PEOR caso razonable de esa llamada quepa (entrada = 1,5 × la
    mayor entrada vista en ese modelo, mínimo 8.000 tokens, más la salida máxima configurada). Así no se supera salvo que una llamada
    sea mucho mayor que todas las anteriores; el exceso posible está acotado por esa llamada."""

    budget_usd: float | None
    spent_usd: float = 0.0
    unpriced_calls: int = 0  # llamadas cuyo coste no se pudo calcular (no deberían darse: se exige tarifa con presupuesto)
    refused: int = 0
    max_input_seen: dict[str, int] = field(default_factory=dict)

    def worst_case(self, spec: ModelSpec) -> float:
        tokens_in = max(int(self.max_input_seen.get(spec.id, 0) * 1.5), 8000)
        p = spec.pricing
        rate_in, rate_out = p.input or 0.0, p.output or 0.0
        if p.long_context is not None and tokens_in > p.long_context.above_input_tokens:
            rate_in, rate_out = p.long_context.input or rate_in, p.long_context.output or rate_out
        return (tokens_in * rate_in + spec.max_output_tokens * rate_out) / 1_000_000

    def check(self, spec: ModelSpec) -> None:
        if self.budget_usd is None:
            return
        need = self.worst_case(spec)
        if self.spent_usd + need > self.budget_usd:
            self.refused += 1
            raise LLMError(f"{BUDGET_EXHAUSTED} (USD): gastado {self.spent_usd:.4f} de {self.budget_usd:.4f}; la siguiente llamada "
                           f"de {spec.id} podría costar hasta {need:.4f}; no se envía nada")

    def add(self, spec: ModelSpec, usage: Usage | None) -> None:
        if usage is None:
            self.unpriced_calls += 1
            return
        tokens_in = int(usage.input_tokens or 0) + (0 if spec.adapter != "anthropic" else int(usage.cached_input_tokens or 0))
        self.max_input_seen[spec.id] = max(self.max_input_seen.get(spec.id, 0), tokens_in)
        cost = call_cost(spec, {"input": usage.input_tokens, "cached": usage.cached_input_tokens, "output": usage.output_tokens,
                                "thinking": usage.thinking_tokens, "token_source": "estimated" if usage.estimated else "provider"})
        if cost.usd is None:
            self.unpriced_calls += 1
        else:
            self.spent_usd += cost.usd


class CostLimitedLLM:
    """Envuelve el cliente de un modelo: comprueba el presupuesto global antes de cada llamada y suma su coste después."""

    def __init__(self, inner: LLMClient, spec: ModelSpec, guard: CostGuard) -> None:
        self._inner, self._spec, self._guard = inner, spec, guard
        self.provider, self.model = spec.provider, spec.model

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self._guard.check(self._spec)
        resp = self._inner.complete(system, messages, tools)
        self._guard.add(self._spec, resp.usage)
        return resp


class DryRunLLM:
    """Modelo simulado para `--dry-run`: no llama a nada ni gasta. Responde siempre con una abstención y sin herramientas, para
    validar la configuración, el snapshot, la suite y la escritura de resultados. Sus veredictos no miden calidad."""

    def __init__(self, spec: ModelSpec) -> None:
        self.provider, self.model = "dry-run", spec.model

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        del system, messages, tools
        calls: list[ToolCall] = []
        return LLMResponse(DRY_RUN_TEXT, calls, Usage(0, 0, 0, None, True, 0))


# ----------------------------------------------------------------------------- ejecución
@dataclass
class BenchConfig:
    models: list[ModelSpec]
    snapshot: str
    suite: str = "bench-v1"
    reps: int = 1
    limit: int | None = None
    budget_usd: float | None = None
    output_root: Path = DEFAULT_ROOT
    run_id: str | None = None
    resume: bool = False
    seed: int = DEFAULT_SEED
    arm: str = "legacy"
    agent_arch: str = "v2"  # arquitectura del agente: la del frontal con AGENT_ARCH=v2
    context_mode: str | None = None  # None = AGENT_CONTEXT del entorno (el mismo .env que el frontal); se registra el valor usado
    root: Path = Path(".")
    db_url: str | None = None
    admin_url: str | None = None
    dry_run: bool = False
    progress: Callable[[str], None] | None = None


def secrets_of(models: list[ModelSpec]) -> list[str]:
    return [v for v in (os.environ.get(m.credentials_env, "").strip() for m in models) if len(v) >= 8]


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        text = text.replace(s, "***")
    return text


def _validate(cfg: BenchConfig) -> None:
    if not cfg.models:
        raise BenchError("no hay modelos que comparar")
    if cfg.reps < 1:
        raise BenchError("--reps debe ser ≥ 1")
    if cfg.budget_usd is not None and cfg.budget_usd <= 0:
        raise BenchError("--budget-usd debe ser > 0")
    if cfg.agent_arch not in ARCHS:
        raise BenchError(f"--agent debe ser uno de {ARCHS}, no {cfg.agent_arch!r}")
    if cfg.context_mode is not None and cfg.context_mode not in CONTEXT_MODES:
        raise BenchError(f"--context debe ser uno de {CONTEXT_MODES}, no {cfg.context_mode!r}")
    if cfg.dry_run:
        return
    if cfg.budget_usd is None:
        raise BenchError("una ejecución real exige --budget-usd (tope de gasto en USD)")
    unpriced = [m.id for m in cfg.models if m.pricing.input is None or m.pricing.output is None]
    if unpriced:
        raise BenchError(f"sin tarifa de entrada/salida no se puede garantizar el presupuesto: {unpriced} (complétala en models.yaml)")
    missing = [f"{m.id} ({m.credentials_env})" for m in cfg.models if not m.has_credentials()]
    if missing:
        raise BenchError(f"faltan credenciales: {', '.join(missing)}")


def run_directory(cfg: BenchConfig) -> tuple[str, Path]:
    run_id = cfg.run_id or f"{datetime.now(MADRID):%Y%m%d-%H%M%S}-{cfg.suite}" + ("-dry" if cfg.dry_run else "")
    path = cfg.output_root / run_id
    if path.exists() and not cfg.resume:
        raise BenchError(f"{path} ya existe: usa otro --run-id o --resume para completar lo pendiente")
    if cfg.resume and not (path / BENCH_FILE).exists():
        raise BenchError(f"--resume: no hay {BENCH_FILE} en {path}")
    return run_id, path


def run_bench(cfg: BenchConfig, llm_factory: Callable[[ModelSpec], LLMClient] = build_llm) -> dict[str, Any]:
    """Ejecuta la comparación y escribe `bench.json` + un `results.json` por modelo. Devuelve el contenido de `bench.json`."""
    _validate(cfg)
    suite = load_suite(cfg.suite, cfg.root)
    case_ids = suite_case_ids(suite, cfg.limit)
    run_id, out = run_directory(cfg)
    previous: dict[str, Any] | None = json.loads((out / BENCH_FILE).read_text(encoding="utf-8")) if cfg.resume else None
    context = resolve_context_mode(cfg.context_mode)
    agent = {"arch": cfg.agent_arch, "context_mode": context}
    if previous and ((previous["suite"]["name"], previous["case_ids"], previous["reps"]) != (cfg.suite, case_ids, cfg.reps)
                     or previous.get("agent", agent) != agent):
        raise BenchError("--resume: la suite, los casos, las repeticiones o el agente no coinciden con la ejecución original")
    secrets = secrets_of(cfg.models)
    say = cfg.progress or (lambda _m: None)
    url, manifest = prepare_database(RunConfig(suite=cfg.suite, snapshot=cfg.snapshot, output=out, root=cfg.root, db_url=cfg.db_url,
                                               admin_url=cfg.admin_url))
    order = [m.id for m in cfg.models]
    random.Random(cfg.seed).shuffle(order)
    by_id = {m.id: m for m in cfg.models}
    guard = CostGuard(cfg.budget_usd, spent_usd=float((previous or {}).get("budget", {}).get("spent_usd_calculated", 0.0)))
    out.mkdir(parents=True, exist_ok=True)
    statuses: dict[str, Any] = dict((previous or {}).get("models_status", {}))
    meta: dict[str, Any] = {
        "schema": 1, "run_id": run_id, "kind": "dry_run" if cfg.dry_run else "live", "seed": cfg.seed, "model_order": order,
        "suite": {"name": cfg.suite, "version": suite.get("version"), "categories": suite.get("categories", [])},
        "case_ids": case_ids, "cases": {c["id"]: {k: c[k] for k in ("category", "complexity", "stratum", "turns")}
                                        for c in suite["cases"] if c["id"] in set(case_ids)},
        "reps": cfg.reps, "arm": cfg.arm, "agent": agent,
        "snapshot": {"id": manifest["id"], "as_of": manifest["as_of"], "fingerprint": manifest["fingerprint"]["sha256"],
                     "synthetic": bool(manifest.get("synthetic"))},
        "models": {m.id: m.as_dict() for m in cfg.models}, "commit": commit_id(cfg.root),
        "started_at": (previous or {}).get("started_at") or datetime.now(MADRID).isoformat(timespec="seconds"),
        "fairness": ["mismo snapshot, reloj y herramientas MCP para todos", f"mismo agente para todos ({cfg.agent_arch}, contexto {context}, banderas {cfg.arm})",
                     "herramientas v2 contra el snapshot con «hoy» = día del as_of" if cfg.agent_arch == "v2" else "agente legacy",
                     "caché de prompts de Anthropic desactivada", "orden de modelos barajado con semilla", "casos en orden rotado por repetición",
                     "sin concurrencia; separación mínima entre peticiones por modelo"]}
    for mid in order:
        spec = by_id[mid]
        say(f"== {mid} ({spec.provider}/{spec.model})")
        llm: LLMClient
        try:
            llm = DryRunLLM(spec) if cfg.dry_run else CostLimitedLLM(llm_factory(spec), spec, guard)
            rc = RunConfig(suite=cfg.suite, snapshot=cfg.snapshot, output=out / mid, reps=cfg.reps, max_requests=spec.max_requests,
                           min_interval=0.0 if cfg.dry_run else spec.min_interval_s, fill_missing=cfg.resume, arm=cfg.arm, root=cfg.root,
                           agent_arch=cfg.agent_arch, context_mode=context,
                           db_url=url, progress=_prefixed(say, mid), case_ids=case_ids,
                           extra_meta={"bench": {"run_id": run_id, "model_id": mid, "spec": spec.as_dict(), "dry_run": cfg.dry_run}})
            results = run_eval(rc, _constant(llm))
            stop = results["meta"]["stopped"]
            statuses[mid] = {"status": "stopped" if stop else "complete", "detail": stop, "requests": results["meta"]["budget"]["used"]}
        except Exception as exc:  # noqa: BLE001 - un modelo que falla no invalida a los demás
            statuses[mid] = {"status": "failed", "detail": redact(f"{type(exc).__name__}: {exc}", secrets)[:500]}
        meta["models_status"] = statuses
        meta["budget"] = {"max_usd": cfg.budget_usd, "spent_usd_calculated": round(guard.spent_usd, 6), "refused_calls": guard.refused,
                          "unpriced_calls": guard.unpriced_calls,
                          "note": "gasto CALCULADO con tarifas y tokens del proveedor; no es el importe facturado"}
        meta["finished_at"] = datetime.now(MADRID).isoformat(timespec="seconds")
        (out / BENCH_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return meta


def _prefixed(say: Callable[[str], None], prefix: str) -> Callable[[str], None]:
    def emit(message: str) -> None:
        say(f"  {prefix} {message}")
    return emit


def _constant(llm: LLMClient) -> Callable[[], LLMClient]:
    def factory() -> LLMClient:
        return llm
    return factory


def load_run(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """`bench.json` y los `results.json` de los modelos que llegaron a escribirlos."""
    meta: dict[str, Any] = json.loads((path / BENCH_FILE).read_text(encoding="utf-8"))
    results: dict[str, dict[str, Any]] = {}
    for mid in meta["model_order"]:
        f = path / mid / RESULTS
        if f.exists():
            results[mid] = json.loads(f.read_text(encoding="utf-8"))
    return meta, results
