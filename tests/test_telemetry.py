"""Fase 2A: telemetría local. Sin LLM, sin red, sin PostgreSQL. Comprueba sobre todo lo que NO debe guardarse."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from futsal.agent import telemetry
from futsal.agent.agent import Agent
from futsal.agent.llm import LLMResponse, Message, ToolCall, Usage
from futsal.agent.memory import Turn
from futsal.agent.trace import RunRecord, ToolCallRecord


class Mcp:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}} for n in ("list_competitions", "get_standings")]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        self.calls.append(name)
        if name == "list_competitions":
            return False, json.dumps({"competitions": [{"id": 1, "name": "TERCERA DIVISIÓN FS", "groups": [{"id": 6, "name": "Grupo 3"}]}]})
        return False, json.dumps({"status": "ok", "standings": [{"position": 1, "team": "C.D. DOSA", "points": 10}]})


class Llm:
    provider, model = "fake", "m"

    def __init__(self) -> None:
        self.steps = [LLMResponse("", [ToolCall("t", "get_standings", {"group_id": 6})], Usage(100, 5, 0, 105)), LLMResponse("Lidera el C.D. DOSA con 10 puntos.", [], Usage(150, 9, 0, 159))]

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        return self.steps.pop(0)


def run_agent(question: str = "¿Quién lidera la clasificación de Tercera División FS grupo 3?", history: list[Turn] | None = None, cid: str | None = "conv-secreta-1") -> RunRecord:
    res = asyncio.run(Agent(Llm(), Mcp(), 5).run(question, history or [], conversation_id=cid))
    return res.run


# --- apagada por defecto ----------------------------------------------------------------------------------------------------------------
def test_off_by_default_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENT_TELEMETRY", raising=False)
    monkeypatch.setenv("AGENT_TELEMETRY_DIR", str(tmp_path / "t"))
    run_agent()
    assert not (tmp_path / "t").exists() and not telemetry.enabled()


def test_the_module_has_no_network_code() -> None:
    src = Path(telemetry.__file__).read_text(encoding="utf-8")
    for forbidden in ("import httpx", "import requests", "import socket", "import urllib", "import smtplib", "http.client"):
        assert forbidden not in src


# --- qué se guarda y qué no -------------------------------------------------------------------------------------------------------------
def test_on_records_exactly_the_documented_fields_and_nothing_sensitive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_TELEMETRY", "on")
    monkeypatch.setenv("AGENT_TELEMETRY_DIR", str(tmp_path))
    history = [Turn("pregunta anterior SECRETA-H", "respuesta anterior SECRETA-R")]
    rec = run_agent(history=history)
    row = json.loads((tmp_path / "questions.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert set(row) == {"schema", "ts", "run_id", "conversation", "question", "pii_flag", "history_turns", "tools_used", "tools_refused", "prefetch", "llm_calls",
                        "mcp_calls", "tokens", "latency_ms", "prompt_mode", "flags", "router", "guardrail", "error_kind"}
    assert row["run_id"] == rec.run_id and row["history_turns"] == 1 and row["tools_used"] == {"get_standings": 1} and row["tokens"]["input"] == 250
    assert row["tokens"]["source"] == "provider" and row["flags"]["AGENT_PREFETCH"] == "off" and row["prompt_mode"] == "legacy" and row["error_kind"] == "none"
    assert set(row["router"]) == {"intent", "tier", "confidence", "reason", "tier0_eligible"}  # sin las entidades detectadas
    blob = json.dumps(row, ensure_ascii=False)
    for secret in ("SECRETA-H", "SECRETA-R", "Lidera el C.D. DOSA", "conv-secreta-1", "group_id"):  # historial, respuesta, id de conversación y argumentos
        assert secret not in blob
    assert row["conversation"] and len(row["conversation"]) == 16 and (tmp_path / ".salt").exists()


def test_conversation_hash_is_stable_per_salt_and_never_the_raw_id(tmp_path: Path) -> None:
    a = telemetry.conversation_hash("c-1", tmp_path)
    assert a == telemetry.conversation_hash("c-1", tmp_path) != telemetry.conversation_hash("c-2", tmp_path) and a != "c-1"
    other = tmp_path / "otra"
    assert telemetry.conversation_hash("c-1", other) != a  # otra sal, otro hash
    assert telemetry.conversation_hash(None, tmp_path) is None


@pytest.mark.parametrize("text,label", [
    ("escríbeme a juan.perez@correo.es", "[email]"), ("mira https://web.es/x?id=3", "[url]"), ("mi DNI es 12345678Z", "[dni]"), ("mi NIE X1234567L", "[dni]"),
    ("llámame al 612 345 678", "[tel]"), ("+34 699123456", "[tel]"), ("IBAN ES9121000418450200051332", "[iban]"), ("sígueme @juan_pz", "[usuario]"),
    ("mi tarjeta 4111 1111 1111 1111", "[num]")])
def test_contact_data_is_replaced_and_flagged(text: str, label: str) -> None:
    clean, flagged = telemetry.scrub(text)
    assert label in clean and flagged
    for raw in ("juan.perez", "12345678Z", "612 345 678", "9121000418450200051332", "4111 1111", "juan_pz", "web.es"):
        assert raw not in clean


@pytest.mark.parametrize("text", ["me llamo Pedro y quiero saber el próximo partido", "mi hijo juega en el DOSA, ¿cuándo juega?", "¿Cuántos goles lleva Zacarías Pimpollo?"])
def test_doubtful_personal_data_is_flagged_not_silently_kept(text: str) -> None:
    assert telemetry.scrub(text)[1] is True


@pytest.mark.parametrize("text", ["¿Quién lidera la clasificación de Tercera División FS grupo 3?", "¿Cuánto dura cada mitad del partido?", "Resultados de la jornada 7"])
def test_ordinary_competition_questions_are_not_flagged(text: str) -> None:
    clean, flagged = telemetry.scrub(text)
    assert clean == text and flagged is False


def test_a_very_long_question_is_truncated_and_flagged() -> None:
    clean, flagged = telemetry.scrub("a " * 400)
    assert len(clean) <= telemetry.MAX_QUESTION_CHARS + 1 and flagged


def test_secrets_never_reach_the_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_TELEMETRY", "on")
    monkeypatch.setenv("AGENT_TELEMETRY_DIR", str(tmp_path))
    run_agent("¿Qué hay con AIzaSyA1234567890abcdefghijklmnopqrstu y la clasificación?")
    assert "AIzaSy" not in (tmp_path / "questions.jsonl").read_text(encoding="utf-8")


def test_a_failure_to_write_never_breaks_the_answer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    blocker = tmp_path / "ocupado"
    blocker.write_text("x", encoding="utf-8")  # un fichero donde debería haber carpeta
    monkeypatch.setenv("AGENT_TELEMETRY", "on")
    monkeypatch.setenv("AGENT_TELEMETRY_DIR", str(blocker / "sub"))
    res = asyncio.run(Agent(Llm(), Mcp(), 5).run("¿Quién lidera?"))
    assert res.answer == "Lidera el C.D. DOSA con 10 puntos." and res.error is None


def test_prefetch_calls_are_recorded_separately_from_the_llms_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rec = RunRecord(question="¿Cómo va el DOSA?", prefetch={"attempted": True, "fired": True, "reason": "ok", "rows": 1, "injected": True, "catalog_calls": 3, "team": "C.D. DOSA"})
    rec.tool_calls = [ToolCallRecord(1, 0, "find_teams", {"name": "C.D. DOSA"}, 1.0, False, 10, 10, "ok", False, via="prefetch"),
                      ToolCallRecord(2, 1, "list_matches", {"team_id": 5}, 1.0, False, 10, 10, "ok", False)]
    row = telemetry.build_record(rec, tmp_path)
    assert row["tools_used"] == {"list_matches": 1} and row["prefetch"] == {"attempted": True, "fired": True, "reason": "ok", "rows": 1, "injected": True, "catalog_calls": 3}
    assert "C.D. DOSA" not in json.dumps(row) and row["mcp_calls"]["prefetch"] == 1 and row["mcp_calls"]["requested_by_llm"] == 1


# --- retención, borrado y exportación ---------------------------------------------------------------------------------------------------
def write_rows(folder: Path, rows: list[dict[str, Any]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "questions.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def row(ts: str, q: str, pii: bool = False, tools: dict[str, int] | None = None) -> dict[str, Any]:
    return {"ts": ts, "question": q, "pii_flag": pii, "tools_used": tools or {}, "router": {"intent": "standings", "tier": "tier0"}, "prefetch": None}


def test_automatic_retention_drops_old_records(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_TELEMETRY", "on")
    monkeypatch.setenv("AGENT_TELEMETRY_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_TELEMETRY_RETENTION_DAYS", "30")
    monkeypatch.setattr(telemetry, "_last_prune", 0.0)
    now = datetime.now(UTC)
    write_rows(tmp_path, [row((now - timedelta(days=45)).isoformat(), "antigua"), row((now - timedelta(days=5)).isoformat(), "reciente")])
    assert telemetry.record(RunRecord(question="nueva pregunta de prueba")) is True
    qs = [r["question"] for r in telemetry._read(tmp_path)]
    assert "antigua" not in qs and "reciente" in qs and "nueva pregunta de prueba" in qs
    assert telemetry.retention_days() == 30
    monkeypatch.setenv("AGENT_TELEMETRY_RETENTION_DAYS", "0")
    assert telemetry.retention_days() == telemetry.DEFAULT_RETENTION_DAYS  # valores no válidos: el predeterminado, nunca «sin límite»


def test_purge_by_date_and_everything(tmp_path: Path) -> None:
    write_rows(tmp_path, [row("2026-01-01T10:00:00+00:00", "a"), row("2026-06-01T10:00:00+00:00", "b"), row("2026-10-01T10:00:00+00:00", "c")])
    assert telemetry.purge(tmp_path, date(2026, 6, 1)) == 1 and [r["question"] for r in telemetry._read(tmp_path)] == ["b", "c"]
    assert telemetry.purge(tmp_path, everything=True) == 2 and not (tmp_path / "questions.jsonl").exists()
    assert telemetry.purge(tmp_path, everything=True) == 0


def test_export_candidates_excludes_flagged_duplicates_and_identifiers(tmp_path: Path) -> None:
    write_rows(tmp_path, [row("2026-10-01T10:00:00+00:00", "¿Quién lidera la clasificación del grupo 3?", tools={"get_standings": 1}),
                          row("2026-10-02T10:00:00+00:00", "¿quién lidera la clasificación del grupo 3?"),  # duplicada
                          row("2026-10-03T10:00:00+00:00", "mi hijo juega en el DOSA, ¿cuándo juega?", pii=True),  # con duda de datos personales
                          row("2026-10-04T10:00:00+00:00", "escríbeme a [email]"), row("2026-10-05T10:00:00+00:00", "hola"),
                          row("2026-10-06T10:00:00+00:00", "¿Cuánto dura cada mitad del partido?", tools={"search_rules": 1})])
    out = tmp_path / "c.jsonl"
    counts = telemetry.export_candidates(tmp_path, out)
    cands = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert counts == {"read": 6, "exported": 2, "excluded_pii_or_duplicate_or_short": 4}
    assert [c["observed_tools"] for c in cands] == [["get_standings"], ["search_rules"]] and all(c["labels"] is None and c["source"] == "real_anonymized" for c in cands)
    assert all(set(c) == {"id", "question", "source", "observed_tools", "observed_router_intent", "labels"} for c in cands)  # sin run_id, fecha ni conversación


def test_cli_stats_purge_and_export(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("AGENT_TELEMETRY_DIR", str(tmp_path))
    write_rows(tmp_path, [row("2026-10-01T10:00:00+00:00", "¿Quién lidera la clasificación del grupo 3?", tools={"get_standings": 1})])
    assert telemetry.main(["stats"]) == 0 and '"records": 1' in capsys.readouterr().out
    assert telemetry.main(["export-candidates"]) == 0 and (tmp_path / "candidates.jsonl").exists()
    assert telemetry.main(["purge", "--all"]) == 0 and not (tmp_path / "questions.jsonl").exists()
    with pytest.raises(SystemExit):
        telemetry.main(["purge"])
