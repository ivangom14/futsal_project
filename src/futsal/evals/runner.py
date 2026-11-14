"""Runner de Futsito-Eval v1: mismo snapshot, mismo reloj, mismas herramientas; repeticiones en orden rotado, tope duro de peticiones
y parada limpia por cuota (los pendientes quedan `not_run` y se completan con `--fill-missing`).

Reutiliza `Agent`, `PacedLLM`, `BudgetedLLM`, `LLMQuotaError` (vía el texto del error del agente), `RunRecord.totals()` y, para la
suite `regression`, el `evaluate` actual sin cambios.
"""

import asyncio
import json
import os
import subprocess
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session as connect
from sqlalchemy import Engine, create_engine, text

from futsal.agent.agent import (
    DEFAULT_MAX_ROWS,
    DEFAULT_MAX_TOOL_CALLS,
    Agent,
    resolve_arch,
    resolve_context_mode,
)
from futsal.agent.arms import arm_env, current_flags
from futsal.agent.llm import BUDGET_EXHAUSTED, QUOTA_MARKER, BudgetedLLM, LLMClient, PacedLLM
from futsal.agent.mcp_client import McpClient, SessionMcpClient
from futsal.agent.memory import Turn
from futsal.api.app import create_app
from futsal.evals import report as reporting
from futsal.evals.catalog import Catalog
from futsal.evals.datasets import counts, suite_cases
from futsal.evals.grading import grade_turn
from futsal.evals.prices import cost_usd, has_price, price_info
from futsal.evals.selector import ResolvedCase, ResolvedTurn, resolve_case
from futsal.evals.snapshot import SnapshotError, load_manifest, restore_snapshot, verify_db
from futsal.mcp_server.server import create_server
from futsal.mcp_server.v2_tools import V2_TOOL_NAMES

RESULTS = "results.json"
REPORT = "report.md"
INFRA = "infra:"
CLOCK_DEPENDENCIES = [
    ("FIJADO: `Agent._clock` (agent/agent.py:455) = as_of del snapshot. Solo lo lee la frescura del Tier 0 "
     "(`tier0_compose(now=self._clock())`, agent.py:1026); con `AGENT_TIER0=off` (predeterminado) no interviene."),
    ("FIJADO: `create_app(today=...)` de la API (api/app.py:76; `mark_past_due` marca como vencidos los partidos programados anteriores "
     "a «hoy») recibe el día del as_of. Es la dependencia que sí cambia respuestas («este fin de semana», partidos vencidos)."),
    ("NO CONTROLABLE sin tocar el agente: la suite `regression` ejecuta el `evaluate` actual, que construye `Agent(...)` por su cuenta "
     "y no admite reloj; con Tier 0 apagado (predeterminado) es indiferente."),
    ("NO CONTROLABLE, solo metadatos: `RunRecord.started_at` (agent/trace.py:80), `telemetry.record` (ts y retención; apagada en la evaluación "
     "con AGENT_TELEMETRY=off), `tier0.py:119` y `presentation.build_sources` (`now or datetime.now(UTC)`: no se usan en la evaluación), "
     "`time.perf_counter`/`time.monotonic` (latencias y ritmo)."),
]


class FingerprintMismatch(RuntimeError):
    """La base de datos no coincide con la huella del snapshot: no se ejecuta nada."""


@dataclass
class RunConfig:
    suite: str
    snapshot: str
    output: Path
    split: str = "dev"
    reps: int = 1
    max_requests: int = 0
    min_interval: float = 0.0
    fill_missing: bool = False
    arm: str = "legacy"
    root: Path = Path(".")
    db_url: str | None = None  # BD ya restaurada; si falta se restaura el volcado en `futsal_eval_<id>`
    admin_url: str | None = None
    progress: Callable[[str], None] | None = None
    extra_meta: dict[str, Any] = field(default_factory=dict)
    case_ids: list[str] | None = None  # subconjunto ordenado de la suite (benchmark: --limit); None = la suite entera
    agent_arch: str | None = None  # legacy | v2; None = AGENT_ARCH del entorno (como siempre)
    context_mode: str | None = None  # legacy | deterministic | llm; None = AGENT_CONTEXT del entorno
    live_manifest: dict[str, Any] | None = None  # solo con snapshot=live: el mismo «hoy» y huella para todos los modelos


# ----------------------------------------------------------------------------- utilidades
def rotated(items: list[Any], rep: int, reps: int) -> list[Any]:
    """Orden rotado por repetición: la repetición r empieza en la posición r·n/reps (cada caso ocupa posiciones distintas)."""
    shift = (rep * len(items)) // reps if reps > 0 and items else 0
    return items[shift:] + items[:shift]


def _git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def commit_id(root: Path) -> str:
    head = _git(root, "rev-parse", "HEAD")
    if not head:
        return "unknown"
    return head + ("-dirty" if _git(root, "status", "--porcelain") else "")


@contextmanager
def arm_environment(arm: str) -> Iterator[dict[str, str]]:
    """Aplica TODAS las banderas del brazo (no hereda nada del entorno) mientras dura la ejecución."""
    env = arm_env(arm)
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        yield current_flags()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@asynccontextmanager
async def mounted(engine: Engine, as_of: datetime) -> AsyncIterator[McpClient]:
    """API real + MCP real en memoria sobre el snapshot, con «hoy» = día del as_of."""
    day = as_of.date()
    server = create_server(TestClient(create_app(engine, today=lambda: day)))
    async with connect(server._mcp_server) as session:
        yield SessionMcpClient(session)


LIVE = "live"  # «snapshot» especial: la BD actual sin congelar (solo lectura); el informe lo marca y avisa de cambios durante la ejecución


def live_manifest(url: str, as_of: str | None = None) -> dict[str, Any]:
    """Manifiesto de la BD VIVA: «hoy» = `as_of` (o ahora, en Europe/Madrid), huella del contenido en este momento (solo lectura)
    y semilla fija `live` para elegir las mismas entidades mientras los datos no cambien."""
    from futsal.evals.snapshot import MADRID, fingerprint, parse_as_of

    engine = create_engine(url)
    try:
        moment = parse_as_of(as_of) if as_of else datetime.now(MADRID).replace(microsecond=0)
        with engine.connect() as conn:
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        return {"id": LIVE, "as_of": moment.isoformat(), "fingerprint": {"sha256": LIVE}, "live": True, "synthetic": False,
                "alembic_revision": revision, "data_fingerprint": fingerprint(engine)}
    finally:
        engine.dispose()


def prepare_database(cfg: RunConfig) -> tuple[str, dict[str, Any]]:
    """Manifiesto + BD del snapshot con la huella verificada. Si no coincide, `FingerprintMismatch`: no se ejecuta nada.
    Con `snapshot = live`: la BD actual (`db_url` o `admin_url`) sin verificar huella; `live_manifest` fijado por quien llama."""
    if cfg.snapshot == LIVE:
        url = cfg.db_url or cfg.admin_url
        if not url:
            raise SnapshotError("modo sin snapshot: falta DATABASE_URL")
        return url, cfg.live_manifest or live_manifest(url)
    manifest = load_manifest(cfg.snapshot, cfg.root)
    if cfg.db_url:
        url = cfg.db_url
        ok, detail = verify_db(manifest, url)
    else:
        if not cfg.admin_url:
            raise SnapshotError("falta --db (BD ya restaurada) o DATABASE_URL para restaurar el volcado")
        restored = restore_snapshot(cfg.snapshot, cfg.admin_url, cfg.root)
        url, ok, detail = restored.url, restored.ok, restored.detail
    if not ok:
        raise FingerprintMismatch(f"la base de datos NO es el snapshot «{cfg.snapshot}»: {detail}")
    return url, manifest


# ----------------------------------------------------------------------------- expectativa según la arquitectura
def expected_for(turn: ResolvedTurn, arch: str, v2_tools: frozenset[str]) -> ResolvedTurn:
    """Expectativa con la que se califica el turno. `legacy`: la de siempre. `v2`: si el caso declara qué haría una arquitectura que SÍ
    resuelve la consulta (`expect.v2`, verdad SQL), esa conducta y esos hechos; las restricciones propias de la abstención de v1
    (prohibir marcadores, herramientas necesarias, menciones de la aclaración) dejan de aplicarse cuando la conducta cambia. Las
    herramientas v2 cuentan como permitidas (no son «inesperadas»)."""
    if arch != "v2":
        return turn
    extra: dict[str, Any] = {}
    if turn.tools_allowed is not None:
        extra["tools_allowed"] = sorted(set(turn.tools_allowed) | v2_tools)
    if not turn.v2:
        return replace(turn, **extra)
    v2 = turn.v2
    behavior = str(v2["behavior"])
    if behavior != turn.behavior:
        extra.update(forbid_any_score=False, forbid_scores=[], tools_needed=[],
                     clarify_mentions=turn.clarify_mentions if behavior == "clarify" else [])
    return replace(turn, behavior=behavior, facts=list(v2.get("facts") or []),
                   forbid_phrases=list(v2.get("forbid_phrases") or turn.forbid_phrases), **extra)


# ----------------------------------------------------------------------------- registros
def _usage(rec: Any, price: tuple[str | None, str | None]) -> dict[str, Any]:
    t = rec.totals()
    metrics = {
        "llm_calls": t["llm_calls"], "tool_calls": t["tool_calls"], "context_calls": t["mcp_calls"]["context_preload"],
        "mcp_calls": t["tool_calls"] + t["mcp_calls"]["context_preload"],
        "input_tokens": t["input_tokens"], "cached_tokens": t["cached_input_tokens"], "output_tokens": t["output_tokens"],
        "thinking_tokens": t["thinking_tokens"], "billed_output_tokens": t["billed_output_tokens"],
        "token_source": t["token_source"], "retries": t["llm_retries"], "errors": t["mcp_calls"]["errors"],
        "exec_tier": t["exec_tier"]}
    metrics["cost_usd"] = cost_usd(price[0], price[1], t)
    timing = {"llm_call_ms": [c.latency_ms for c in rec.llm_calls], "tool_call_ms": [c.duration_ms for c in rec.tool_calls],
              "latency_ms": t["latency_ms"], "basis": "call",
              "llm_paced_ms": [c.paced_ms for c in rec.llm_calls], "llm_retry_wait_ms": [c.retry_wait_ms for c in rec.llm_calls]}
    # Detalle por llamada (benchmark multimodelo): tokens de ESA llamada tal como los informó el proveedor (o estimados) y reintentos.
    # Solo hechos deterministas (los tiempos van en `timing`). El coste lo calcula quien tenga la tarifa (bench/registry.py).
    detail = [{"index": c.index, "input": c.input_tokens, "cached": c.cached_input_tokens, "output": c.output_tokens,
               "thinking": c.thinking_tokens, "token_source": c.token_source, "retries": c.retries,
               "error": (c.error or None) and str(c.error)[:200]}
              for c in rec.llm_calls]
    return {"metrics": metrics, "timing": timing, "llm_calls_detail": detail}


def _empty_usage() -> dict[str, Any]:
    return {"metrics": {"llm_calls": 0, "tool_calls": 0, "context_calls": 0, "mcp_calls": 0, "input_tokens": 0, "cached_tokens": 0,
                        "output_tokens": 0, "thinking_tokens": 0, "billed_output_tokens": 0, "token_source": "unreported",
                        "retries": 0, "errors": 0, "exec_tier": "", "cost_usd": None},
            "timing": {"llm_call_ms": [], "tool_call_ms": [], "latency_ms": 0.0, "basis": "call"}, "llm_calls_detail": []}


def _record(rc_id: str, stratum: str, holdout: bool, rep: int, idx: int, question: str, verdict: str, reason: str = "",
            **extra: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {"case_id": rc_id, "stratum": stratum, "holdout": holdout, "rep": rep, "turn": idx, "question": question,
                           "verdict": verdict, "error_class": None, "reason": reason, "answer": "", "tools_used": [],
                           "grade": None, "run_id": "", **_empty_usage()}
    rec.update(extra)
    return rec


def _payloads(rec: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tc in rec.tool_calls:
        if not tc.is_error and tc.result:
            try:
                data = json.loads(tc.result)
            except ValueError:
                continue
            if isinstance(data, dict):
                out.append(data)
    return out


def _stop_reason(error: str | None) -> str | None:
    if error and QUOTA_MARKER in error:
        return "cuota del proveedor agotada"
    if error and BUDGET_EXHAUSTED in error:
        return "presupuesto de peticiones agotado"
    return None


async def run_conversation(rc: ResolvedCase, rep: int, mcp: McpClient, llm: LLMClient, as_of: datetime, players: list[str], names: list[str],
                           price: Any, stopped: str | None,
                           agent_kwargs: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Ejecuta los turnos de un caso (nuevo `Agent` por conversación). Devuelve los registros y, si procede, el motivo de parada.

    `agent_kwargs`: arquitectura, modo de contexto y registro v2 (contra el snapshot) que fija `run_eval`."""
    records: list[dict[str, Any]] = []
    agent = Agent(llm, mcp, DEFAULT_MAX_TOOL_CALLS, max_rows=DEFAULT_MAX_ROWS, trace_results=True, **(agent_kwargs or {}))
    agent._clock = lambda: as_of  # reloj fijo del snapshot (solo lo usa la frescura de Tier 0)
    history: list[Turn] = []
    focus: dict[str, Any] = {}
    state: dict[str, Any] | None = None
    halted = stopped  # parada de esta conversación (la global solo si es cuota o presupuesto)
    for i, turn in enumerate(rc.turns):
        if halted:
            records.append(_record(rc.case_id, rc.stratum, rc.holdout, rep, i, turn.user, "not_run", halted))
            continue
        result = await (agent.run(turn.user, history, focus, f"{rc.case_id}#{rep}", state) if state
                        else agent.run(turn.user, history, focus, f"{rc.case_id}#{rep}"))
        run = result.run
        assert run is not None
        usage = _usage(run, price)
        infra = bool(result.error) and bool(run.llm_calls) and bool(run.llm_calls[-1].error)
        if infra:
            hard = _stop_reason(result.error)
            if hard:
                stopped = halted = hard
                records.append(_record(rc.case_id, rc.stratum, rc.holdout, rep, i, turn.user, "not_run", hard,
                                       run_id=run.run_id, **usage))
            else:
                halted = f"{INFRA} turno anterior sin respuesta"
                records.append(_record(rc.case_id, rc.stratum, rc.holdout, rep, i, turn.user, "not_evaluable",
                                       f"{INFRA} {result.error}", run_id=run.run_id, **usage))
            continue
        evidence = _payloads(run)
        grade = grade_turn(turn, result.answer, [tc.name for tc in run.tool_calls], evidence,
                           usage["metrics"]["llm_calls"], players, names)
        records.append(_record(
            rc.case_id, rc.stratum, rc.holdout, rep, i, turn.user, grade.verdict, "; ".join(grade.reasons),
            error_class=grade.error_class, answer=result.answer, tools_used=[tc.name for tc in run.tool_calls],
            grade=grade.to_dict(), run_id=run.run_id, evidence=evidence, **usage))  # evidencia: permite recalificar sin el LLM
        history.append(Turn(turn.user, result.answer))
        focus = result.focus or {}
        state = getattr(result, "state", None)
    return records, stopped


def _unevaluable(rc: ResolvedCase, case_turns: list[str], rep: int) -> list[dict[str, Any]]:
    return [_record(rc.case_id, rc.stratum, rc.holdout, rep, i, u, "not_evaluable", rc.reason) for i, u in enumerate(case_turns)]


# ----------------------------------------------------------------------------- fusión de resultados previos (--fill-missing)
def _pending_pairs(previous: dict[str, Any] | None, pairs: list[tuple[str, int]]) -> set[tuple[str, int]]:
    """Pares (caso, repetición) que hay que ejecutar: todos, o los que no terminaron en un results.json anterior."""
    if previous is None:
        return set(pairs)
    open_: set[tuple[str, int]] = set()
    seen: dict[tuple[str, int], bool] = {}
    for r in previous["records"]:
        k = (r["case_id"], r["rep"])
        redo = r["verdict"] == "not_run" or (r["verdict"] == "not_evaluable" and str(r["reason"]).startswith(INFRA))
        seen[k] = seen.get(k, False) or redo
    for p in pairs:
        if p not in seen or seen[p]:
            open_.add(p)
    return open_


def _load_previous(cfg: RunConfig, manifest: dict[str, Any]) -> dict[str, Any] | None:
    """results.json anterior de `--output` (solo con --fill-missing). Debe ser del mismo snapshot, suite, división y brazo."""
    path = cfg.output / RESULTS
    if not (cfg.fill_missing and path.exists()):
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    old = data["meta"]
    mismatch = [name for name, same in (
        ("snapshot", old["snapshot"]["fingerprint"] == manifest["fingerprint"]["sha256"]), ("suite", old["suite"] == cfg.suite),
        ("split", old["split"] == cfg.split), ("brazo", old["arm"] == cfg.arm),
        ("agente", old.get("agent", {}).get("arch", resolve_arch(None)) == resolve_arch(cfg.agent_arch))) if not same]
    if mismatch:
        raise ValueError(f"--fill-missing: el results.json de {cfg.output} no coincide en {', '.join(mismatch)}: no se mezclan ejecuciones")
    return data


# ----------------------------------------------------------------------------- ejecución
def run_eval(cfg: RunConfig, llm_factory: Callable[[], LLMClient]) -> dict[str, Any]:
    """Ejecuta una suite y escribe `results.json` y `report.md` en `cfg.output`. Devuelve el diccionario de resultados."""
    if cfg.suite == "regression":
        from futsal.evals.regression import run_regression
        return run_regression(cfg, llm_factory)
    if cfg.reps < 1:
        raise ValueError("--reps debe ser ≥ 1")
    url, manifest = prepare_database(cfg)
    engine = create_engine(url)
    try:
        as_of = datetime.fromisoformat(manifest["as_of"])
        cat = Catalog.load(engine, seed=manifest["fingerprint"]["sha256"], as_of=as_of)
        cases = suite_cases(cfg.suite, cfg.split, cfg.root)
        if cfg.case_ids is not None:
            by_case = {c.id: c for c in cases}
            missing = [i for i in cfg.case_ids if i not in by_case]
            if missing:
                raise ValueError(f"casos que no están en la suite {cfg.suite}: {missing[:5]}")
            cases = [by_case[i] for i in cfg.case_ids]
        caps = cat.capabilities()
        resolved = {c.id: resolve_case(c, cat, caps) for c in cases}
        by_id = {c.id: c for c in cases}
        order = [c.id for c in cases]
        pairs = [(cid, r) for r in range(cfg.reps) for cid in order]
        previous = _load_previous(cfg, manifest)
        todo = _pending_pairs(previous, pairs)
        arch, context = resolve_arch(cfg.agent_arch), resolve_context_mode(cfg.context_mode)
        agent_kwargs: dict[str, Any] = {"arch": arch, "context_mode": context}
        # v1 y v2 pasan por el MCP y la API montados sobre el snapshot (con «hoy» = día del as_of): nunca tocan DATABASE_URL.
        v2_tools = frozenset(V2_TOOL_NAMES) if arch == "v2" else frozenset()
        resolved = {cid: replace(rc, turns=[expected_for(t, arch, v2_tools) for t in rc.turns]) for cid, rc in resolved.items()}
        base = llm_factory()
        budget = BudgetedLLM(PacedLLM(base, cfg.min_interval), cfg.max_requests)
        price = (getattr(base, "provider", None), getattr(base, "model", None))
        say = cfg.progress or (lambda _m: None)

        async def go() -> tuple[list[dict[str, Any]], str | None]:
            out: list[dict[str, Any]] = []
            stopped: str | None = None
            async with mounted(engine, as_of) as mcp:
                for rep in range(cfg.reps):
                    for cid in rotated(order, rep, cfg.reps):
                        if (cid, rep) not in todo:
                            continue
                        rc = resolved[cid]
                        if rc.status != "ok":
                            out += _unevaluable(rc, [t.user for t in by_id[cid].turns], rep)
                            continue
                        say(f"[rep {rep + 1}/{cfg.reps}] {cid}")
                        recs, stopped = await run_conversation(rc, rep, mcp, budget, as_of, cat.player_names, cat.known_names(), price, stopped,
                                                               agent_kwargs)
                        out += recs
            return out, stopped

        with arm_environment(cfg.arm) as flags:
            new_records, stop = asyncio.run(go())
        kept = [r for r in (previous["records"] if previous else []) if (r["case_id"], r["rep"]) not in todo]
        records = sorted(kept + new_records, key=lambda r: (r["rep"], r["case_id"], r["turn"]))
        stop = stop or next((r["reason"] for r in records if r["verdict"] == "not_run"), None)
        meta = build_meta(cfg, manifest, base, flags, budget.used, stop, price)
        meta["agent"] = {"arch": arch, "context_mode": context}
        results = {"schema": 1, "meta": meta,
                   "cases": {cid: _case_info(resolved[cid], by_id[cid].turns) for cid in order},
                   "records": records}
        results["summary"] = reporting.summarize(results)
        write_outputs(cfg.output, results)
        return results
    finally:
        engine.dispose()


def _case_info(rc: ResolvedCase, turns: Any) -> dict[str, Any]:
    return {"stratum": rc.stratum, "holdout": rc.holdout, "capability": rc.capability, "status": rc.status, "reason": rc.reason,
            "bindings": rc.bindings, "expected": [t.to_dict() for t in rc.turns] or [{"user": t.user} for t in turns]}


def build_meta(cfg: RunConfig, manifest: dict[str, Any], llm: Any, flags: dict[str, str], used: int, stopped: str | None,
               price: tuple[str | None, str | None]) -> dict[str, Any]:
    from futsal.evals.snapshot import MADRID
    return {
        "suite": cfg.suite, "split": cfg.split, "reps": cfg.reps, "arm": cfg.arm, "agent_flags": flags,
        "snapshot": {"id": manifest["id"], "as_of": manifest["as_of"], "fingerprint": manifest["fingerprint"]["sha256"],
                     "synthetic": bool(manifest.get("synthetic")), "alembic_revision": manifest.get("alembic_revision"),
                     "live": bool(manifest.get("live"))},
        "model": {"provider": getattr(llm, "provider", None), "model": getattr(llm, "model", None)},
        "commit": commit_id(cfg.root), "generated_at": datetime.now(MADRID).isoformat(timespec="seconds"),
        "budget": {"max_requests": cfg.max_requests, "used": used, "min_interval_s": cfg.min_interval},
        "stopped": stopped, "temperature": "predeterminada del proveedor (el agente no la fija)",
        "clock": {"as_of": manifest["as_of"], "dependencies": CLOCK_DEPENDENCIES},
        "prices": price_info(price[0], price[1]), "priced": has_price(price[0], price[1]),
        "dataset": counts(suite_cases(cfg.suite, cfg.split, cfg.root)) if cfg.suite != "regression" else None, **cfg.extra_meta}


def write_outputs(output: Path, results: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / RESULTS).write_text(json.dumps(results, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (output / REPORT).write_text(reporting.render_markdown(results), encoding="utf-8")
