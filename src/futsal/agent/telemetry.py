"""Telemetría LOCAL de uso (Fase 2A): para medir qué preguntas llegan de verdad y qué herramientas se usan, sin coste de tokens.

Apagada por defecto (`AGENT_TELEMETRY=off`). Con `on` añade UNA línea por pregunta a `data/telemetry/questions.jsonl` (ignorado por Git).
Este módulo no importa ninguna librería de red: nada sale de la máquina.

SE REGISTRA: fecha y `run_id`; la conversación como hash con sal local; la pregunta SANEADA (contactos y datos personales sustituidos, 300 car. como
máximo) y `pii_flag`; nº de turnos de historial (solo el número); nombres y recuentos de herramientas realmente usadas por el LLM (sin argumentos);
resultado del prefetch (sin texto); llamadas LLM/MCP; tokens del proveedor y su origen; modo de prompt y banderas; decisión del router en sombra
(sin entidades); guardarraíl y clase de error.
NUNCA SE REGISTRA: la pregunta sin sanear, el historial, las respuestas, los argumentos ni los resultados de las herramientas, IDs internos, claves.

Retención: `AGENT_TELEMETRY_RETENTION_DAYS` (90 por defecto) borra automáticamente lo más antiguo (como mucho una vez al día); `purge` borra a mano.
Duda sobre datos personales → `pii_flag` y el registro NO se exporta como candidato a dataset.

CLI: `python -m futsal.agent.telemetry stats | purge (--before AAAA-MM-DD | --all) | export-candidates [--out FICHERO]`.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from futsal.agent.arms import current_flags
from futsal.agent.trace import RunRecord, sanitize_text
from futsal.config.settings import env_value

logger = logging.getLogger("futsal.telemetry")
SCHEMA = 1
DEFAULT_DIR = "data/telemetry"
DEFAULT_RETENTION_DAYS = 90
MAX_QUESTION_CHARS = 300
MIN_EXPORT_CHARS = 8
_lock = threading.Lock()
_last_prune = 0.0

# --- datos personales: lo que se SUSTITUYE (contacto e identificadores) --------------------------------------------------------------
_REPLACE = (
    ("[email]", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("[url]", re.compile(r"(?:https?://|www\.)\S+", re.I)),
    ("[iban]", re.compile(r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]{4}){3,7}(?:[ -]?[A-Z0-9]{1,4})?\b")),
    ("[dni]", re.compile(r"\b(?:\d{8}|[XYZxyz][ -]?\d{7})[ -]?[A-Za-z]\b")),
    ("[tel]", re.compile(r"(?<![\w])(?:\+\d{1,3}[ .-]?)?(?<!\d)[6-9]\d{2}[ .-]?\d{3}[ .-]?\d{3}(?!\d)")),
    ("[usuario]", re.compile(r"(?<![\w@])@[A-Za-z0-9_]{3,}")),
    ("[num]", re.compile(r"(?<!\d)(?:\d[ -]?){9,}\d(?!\d)|(?<!\d)\d{7,}(?!\d)")),
)
# --- lo que NO se sustituye pero levanta `pii_flag` (puede haber datos personales): autoidentificación y posible nombre completo -----
_SELF_ID = re.compile(r"\b(?:me llamo|mi nombre es|soy (?:el|la|un|una)?\s*[A-ZÁÉÍÓÚÑ]|mi (?:hijo|hija|mujer|marido|pareja|madre|padre|hermano|hermana|"
                      r"dirección|domicilio|teléfono|telefono|correo|email)|vivo en|trabajo en)\b", re.I)
_TITLE = re.compile(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñü]{2,}\b")
_NOT_NAMES = frozenset(w.lower() for w in (
    "primera segunda tercera cuarta quinta división division autonómica autonomica aficionado juvenil senior preferente regional copa trofeo comunidad honor "
    "fase final previa grupo grupos eliminatorias único unico liga jornada temporada reglamento circular regla reglas futsal fútbol futbol sala "
    "cuál cuáles cómo cuándo cuánto cuántos cuántas quién quiénes qué dónde dime dame muéstrame enero febrero marzo abril mayo junio julio agosto septiembre "
    "octubre noviembre diciembre lunes martes miércoles jueves viernes sábado domingo españa madrid fifa rfef rffm").split())


def scrub(text: str) -> tuple[str, bool]:
    """(texto saneado, pii_flag). Primero secretos (como las trazas), luego datos personales de contacto; marca la duda sin intentar adivinar nombres."""
    clean = sanitize_text(text or "")
    flagged = clean != (text or "")
    for label, pattern in _REPLACE:
        clean, n = pattern.subn(label, clean)
        flagged = flagged or n > 0
    if _SELF_ID.search(clean):
        flagged = True
    words = [(m.start(), m.group(0)) for m in _TITLE.finditer(clean)]
    for (p1, w1), (p2, w2) in zip(words, words[1:], strict=False):  # dos palabras seguidas con inicial mayúscula que no son vocabulario de la competición
        if clean[p1 + len(w1):p2].strip() == "" and w1.lower() not in _NOT_NAMES and w2.lower() not in _NOT_NAMES and p1 > 0:
            flagged = True
            break
    clean = " ".join(clean.split())
    if len(clean) > MAX_QUESTION_CHARS:
        clean, flagged = clean[:MAX_QUESTION_CHARS].rstrip() + "…", True  # una pregunta tan larga puede arrastrar contenido pegado: se duda
    return clean, flagged


# --- configuración -------------------------------------------------------------------------------------------------------------------
def enabled() -> bool:
    return (env_value("AGENT_TELEMETRY", "off") or "off").strip().lower() == "on"


def telemetry_dir() -> Path:
    return Path(env_value("AGENT_TELEMETRY_DIR", DEFAULT_DIR) or DEFAULT_DIR)


def retention_days() -> int:
    try:
        days = int(env_value("AGENT_TELEMETRY_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS)))
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return days if days >= 1 else DEFAULT_RETENTION_DAYS


def conversation_hash(conversation_id: str | None, folder: Path) -> str | None:
    """Hash con sal LOCAL (el id real nunca se guarda). Sin poder crear la sal no se registra la conversación."""
    if not conversation_id:
        return None
    salt_file = folder / ".salt"
    try:
        if not salt_file.exists():
            folder.mkdir(parents=True, exist_ok=True)
            salt_file.write_text(secrets.token_hex(16), encoding="utf-8")
            try:
                os.chmod(salt_file, 0o600)
            except OSError:
                pass
        salt = salt_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return hashlib.sha256(f"{salt}:{conversation_id}".encode()).hexdigest()[:16]


def _error_kind(rec: RunRecord) -> str:
    if not rec.error:
        return "none"
    low = rec.error.lower()
    return "tool_limit" if "límite" in low or "limite" in low else "llm" if "llm" in low else "other"


def build_record(rec: RunRecord, folder: Path, now: datetime | None = None) -> dict[str, Any]:
    question, pii = scrub(rec.question)
    totals = rec.totals()
    used = Counter(t.name for t in rec.tool_calls if t.via != "prefetch" and not t.refused)
    router = rec.router if isinstance(rec.router, dict) and "error" not in rec.router else None
    pf = rec.prefetch or {}
    return {
        "schema": SCHEMA, "ts": (now or datetime.now(UTC)).isoformat(timespec="seconds"), "run_id": rec.run_id,
        "conversation": conversation_hash(rec.conversation_id, folder), "question": question, "pii_flag": pii,
        "history_turns": rec.history_turns, "tools_used": dict(sorted(used.items())),
        "tools_refused": sum(t.refused for t in rec.tool_calls),
        "prefetch": {k: pf.get(k) for k in ("attempted", "fired", "reason", "rows", "injected", "catalog_calls") if k in pf} or None,
        "llm_calls": totals["llm_calls"], "mcp_calls": totals["mcp_calls"],
        "tokens": {"input": totals["input_tokens"], "output": totals["output_tokens"], "thinking": totals["thinking_tokens"],
                   "cached": totals["cached_input_tokens"], "source": totals["token_source"]},
        "latency_ms": totals["latency_ms"], "prompt_mode": rec.prompt_mode, "flags": current_flags(),
        "router": {k: router.get(k) for k in ("intent", "tier", "confidence", "reason", "tier0_eligible")} if router else None,
        "guardrail": rec.guardrail, "error_kind": _error_kind(rec)}


def questions_file(folder: Path | None = None) -> Path:
    return (folder or telemetry_dir()) / "questions.jsonl"


def record(rec: RunRecord, *, now: datetime | None = None) -> bool:
    """Añade la pregunta a la telemetría si está activada. NUNCA lanza ni afecta a la respuesta."""
    if not enabled():
        return False
    try:
        folder = telemetry_dir()
        folder.mkdir(parents=True, exist_ok=True)
        line = json.dumps(build_record(rec, folder, now), ensure_ascii=False, separators=(",", ":"))
        with _lock:
            with questions_file(folder).open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            _maybe_prune(folder, now)
        return True
    except Exception as exc:  # noqa: BLE001 - la telemetría jamás debe romper una respuesta
        logger.warning("telemetría no escrita (%s)", type(exc).__name__)
        return False


# --- retención y borrado --------------------------------------------------------------------------------------------------------------
def _read(folder: Path) -> list[dict[str, Any]]:
    path = questions_file(folder)
    rows: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _rewrite(folder: Path, rows: list[dict[str, Any]]) -> None:
    path = questions_file(folder)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)


def purge(folder: Path, before: date | None = None, everything: bool = False) -> int:
    """Borra los registros anteriores a `before` (o todos). Devuelve cuántos."""
    rows = _read(folder)
    keep = [] if everything else [r for r in rows if str(r.get("ts", ""))[:10] >= (before or date.min).isoformat()]
    with _lock:
        if everything and questions_file(folder).exists():
            questions_file(folder).unlink()
        elif len(keep) != len(rows):
            _rewrite(folder, keep)
    return len(rows) - len(keep)


def _maybe_prune(folder: Path, now: datetime | None = None) -> None:
    global _last_prune
    if time.monotonic() - _last_prune < 86400 and _last_prune:
        return
    _last_prune = time.monotonic()
    cutoff = ((now or datetime.now(UTC)) - timedelta(days=retention_days())).date()
    rows = _read(folder)
    keep = [r for r in rows if str(r.get("ts", ""))[:10] >= cutoff.isoformat()]
    if len(keep) != len(rows):
        _rewrite(folder, keep)


def stats(folder: Path) -> dict[str, Any]:
    rows = _read(folder)
    return {"records": len(rows), "pii_flagged": sum(bool(r.get("pii_flag")) for r in rows), "first": min((r["ts"] for r in rows), default=None),
            "last": max((r["ts"] for r in rows), default=None), "retention_days": retention_days(),
            "tools": dict(Counter(t for r in rows for t in (r.get("tools_used") or {}))),
            "router_tiers": dict(Counter((r.get("router") or {}).get("tier") for r in rows)),
            "prefetch_fired": sum(bool((r.get("prefetch") or {}).get("fired")) for r in rows)}


def export_candidates(folder: Path, out: Path) -> dict[str, int]:
    """Candidatos a dataset: solo registros SIN `pii_flag`, deduplicados, sin id de ejecución, fecha ni conversación. Hay que etiquetarlos a mano."""
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    rows = _read(folder)
    for r in rows:
        q = str(r.get("question", ""))
        key = " ".join(re.findall(r"\w+", q.lower()))
        if r.get("pii_flag") or len(q) < MIN_EXPORT_CHARS or key in seen or "[" in q:
            continue
        seen.add(key)
        kept.append({"id": "real_" + hashlib.sha1(key.encode()).hexdigest()[:10], "question": q, "source": "real_anonymized",
                     "observed_tools": sorted(r.get("tools_used") or {}), "observed_router_intent": (r.get("router") or {}).get("intent"),
                     "labels": None})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in kept), encoding="utf-8")
    return {"read": len(rows), "exported": len(kept), "excluded_pii_or_duplicate_or_short": len(rows) - len(kept)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Telemetría local de uso (sin red).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p = sub.add_parser("purge")
    p.add_argument("--before", help="AAAA-MM-DD")
    p.add_argument("--all", action="store_true")
    e = sub.add_parser("export-candidates")
    e.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    folder = telemetry_dir()
    if args.cmd == "stats":
        print(json.dumps(stats(folder), ensure_ascii=False, indent=1))
    elif args.cmd == "purge":
        if not args.all and not args.before:
            raise SystemExit("indica --before AAAA-MM-DD o --all")
        n = purge(folder, date.fromisoformat(args.before) if args.before else None, args.all)
        print(f"{n} registros borrados")
    else:
        out = Path(args.out) if args.out else folder / "candidates.jsonl"
        print(json.dumps(export_candidates(folder, out), ensure_ascii=False), f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
