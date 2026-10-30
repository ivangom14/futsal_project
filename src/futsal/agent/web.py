"""Servicio HTTP del chat (Fase 22): aloja el agente real para el frontend web.

Solo habla con el LLM y con el servidor MCP (que a su vez usa la API de datos); nunca con PostgreSQL.
`uvicorn futsal.agent.web:app_factory --factory --port 8001`.
"""

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field, StringConstraints
from typing import Annotated

from futsal.agent.agent import DEFAULT_MAX_ROWS, DEFAULT_MAX_TOOL_CALLS, OFF_TOPIC_MESSAGE, Agent, AgentResult
from futsal.agent.llm import create_llm
from futsal.agent.memory import (
    CONVERSATION_ID,
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_TURNS,
    MAX_STORED_TURNS,
    ConversationMemory,
    Turn,
    select_turns,
    turns_from_client,
)
from futsal.agent.mcp_client import SessionMcpClient, stdio_params
from futsal.agent.trace import RunRecord, redact
from futsal.config.settings import env_value

DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
MAX_QUESTION_CHARS = 2000
MAX_SOURCE_TEXT = 1200
MAX_HISTORY_ITEM_CHARS = 6000
MEMORY_GUARDRAILS = (None, "aclaracion_ambito")  # solo estas respuestas son válidas como contexto
KEY_VARS = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: Annotated[str, StringConstraints(max_length=MAX_HISTORY_ITEM_CHARS)]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    conversation_id: str | None = Field(default=None, pattern=CONVERSATION_ID.pattern)  # sin id, la pregunta es independiente
    # Solo siembra una conversación que el servidor no conoce (p. ej. tras reiniciarlo); si la conoce, manda su propio historial.
    history: list[HistoryItem] = Field(default_factory=list, max_length=2 * MAX_STORED_TURNS)


class Source(BaseModel):
    kind: str  # "rag" (fragmento del reglamento/circulares) | "official_table" (clasificación/goleadores oficiales)
    title: str
    detail: str | None = None
    pages: str | None = None
    text: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ToolInfo(BaseModel):
    name: str
    arguments: dict[str, Any]
    duration_ms: float
    is_error: bool
    refused: bool
    summary: str


class Diagnostics(BaseModel):
    run_id: str
    provider: str | None
    model: str | None
    duration_ms: float
    history_turns: int  # turnos anteriores de la conversación enviados al agente como contexto
    llm_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    token_source: str
    tools: list[ToolInfo]
    errors: list[str]


class ChatResponse(BaseModel):
    answer: str
    guardrail: str | None
    error: str | None  # no vacío = ejecución incompleta (p. ej. límite de herramientas); `answer` sigue siendo el texto seguro
    sources: list[Source]
    diagnostics: Diagnostics


def _json(text: str | None) -> Any:
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def build_sources(rec: RunRecord, official: list[dict[str, Any]]) -> list[Source]:
    """Fuentes realmente recuperadas en esta ejecución (resultados de `search_rules` y tablas oficiales); nada inventado."""
    sources: list[Source] = []
    seen: set[tuple[Any, ...]] = set()
    for call in rec.tool_calls:
        data = _json(call.result) if call.name == "search_rules" and not call.is_error else None
        for hit in data.get("hits", []) if isinstance(data, dict) else []:
            if not isinstance(hit, dict):
                continue
            key = (hit.get("doc_key"), hit.get("rule"), hit.get("section"), hit.get("pages"), str(hit.get("text"))[:80])
            if key in seen:
                continue
            seen.add(key)
            detail = " · ".join(str(x) for x in (
                f"Regla {hit['rule']}" if hit.get("rule") else None, hit.get("rule_title"), hit.get("section"),
                hit.get("part")) if x)
            text = hit.get("text")
            sources.append(Source(
                kind="rag", title=str(hit.get("document") or "Documento"), detail=detail or None,
                pages=hit.get("pages"), text=str(text)[:MAX_SOURCE_TEXT] if text else None,
                meta={k: hit[k] for k in ("version", "type", "doc_key") if hit.get(k)}))
    for data in official:
        src: dict[str, Any] = data["source"] if isinstance(data.get("source"), dict) else {}
        what = "Clasificación oficial" if data.get("standings") else "Goleadores oficiales"
        sources.append(Source(
            kind="official_table", title=f"{what} de la RFFM",
            detail=f"{data.get('competition', '')} · {data.get('group', '')}".strip(" ·") or None,
            meta={k: v for k, v in (("captured_at", src.get("captured_at")), ("round", src.get("round")),
                                    ("status", data.get("status"))) if v is not None}))
    return sources


def build_response(result: AgentResult, history_turns: int = 0) -> ChatResponse:
    rec = result.run or RunRecord()
    totals = rec.totals()
    tools = [ToolInfo(name=t.name, arguments=t.arguments, duration_ms=round(t.duration_ms, 1), is_error=t.is_error,
                      refused=t.refused, summary=t.result_summary) for t in rec.tool_calls]
    errors = [redact(f"herramienta {t.name}: {t.result_summary}") for t in rec.tool_calls if t.is_error]
    errors += [redact(f"llamada LLM {c.index}: {c.error}") for c in rec.llm_calls if c.error]
    diag = Diagnostics(
        run_id=rec.run_id, provider=rec.provider, model=rec.model, duration_ms=round(rec.latency_ms, 1), history_turns=history_turns,
        llm_calls=totals["llm_calls"], tool_calls=totals["tool_calls"], input_tokens=totals["input_tokens"],
        output_tokens=totals["output_tokens"], token_source=totals["token_source"], tools=tools, errors=errors)
    return ChatResponse(answer=result.answer, guardrail=result.guardrail, error=result.error,
                        sources=build_sources(rec, result.official), diagnostics=diag)


class ChatUnavailable(Exception):
    """El agente no está operativo (LLM sin configurar, MCP que no arranca…)."""


AgentFactory = Callable[[], AbstractAsyncContextManager[Agent]]


@asynccontextmanager
async def default_agent() -> AsyncIterator[Agent]:
    """Agente real: LLM según `LLM_PROVIDER` y servidor MCP por stdio. Resultados de tools solo en memoria (sin traza en disco)."""
    llm = create_llm()
    limit = int(env_value("MAX_TOOL_CALLS", str(DEFAULT_MAX_TOOL_CALLS)))
    rows = int(env_value("MAX_RESULT_ROWS", str(DEFAULT_MAX_ROWS)))
    async with stdio_client(stdio_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield Agent(llm, SessionMcpClient(session), limit, max_rows=rows, trace_path=None, trace_results=True,
                  history_turns=int(env_value("HISTORY_MAX_TURNS", str(DEFAULT_MAX_TURNS))),
                  history_chars=int(env_value("HISTORY_MAX_CHARS", str(DEFAULT_MAX_CHARS))))


class AgentRunner:
    """Agente en un hilo con su propio bucle de eventos: la llamada al LLM es síncrona y no debe bloquear `/chat/health`.
    Las preguntas se atienden de una en una (el `Agent` guarda estado por pregunta)."""

    def __init__(self, factory: AgentFactory = default_agent) -> None:
        self._factory = factory
        self._loop: asyncio.AbstractEventLoop | None = None
        self._agent: Agent | None = None
        self._lock: asyncio.Lock | None = None
        self._stop: asyncio.Event | None = None
        self._settled = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    @property
    def ready(self) -> bool:
        return self._agent is not None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._settled.clear()
        self._thread = threading.Thread(target=lambda: asyncio.run(self._serve()), name="futsal-agent", daemon=True)
        self._thread.start()

    async def _serve(self) -> None:
        self._loop, self._lock, self._stop = asyncio.get_running_loop(), asyncio.Lock(), asyncio.Event()
        try:
            async with self._factory() as agent:
                self._agent, self.error = agent, None
                self._settled.set()
                await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 - se informa en /chat/health; el servicio sigue vivo
            self.error = redact(f"{type(exc).__name__}: {exc}")
        finally:
            self._agent = None
            self._settled.set()

    def stop(self) -> None:
        if self._loop is None or self._stop is None or self._thread is None or not self._thread.is_alive():
            return
        try:
            self._loop.call_soon_threadsafe(self._stop.set)
        except RuntimeError:  # el bucle ya terminó (arranque fallido)
            return
        self._thread.join(timeout=10)

    async def ask(self, question: str, history: Sequence[Turn] = ()) -> AgentResult:
        if self._agent is None:  # primer arranque fallido o aún en curso: reintenta
            self.start()
            await asyncio.get_running_loop().run_in_executor(None, self._settled.wait, 30)
        if self._agent is None or self._loop is None:
            raise ChatUnavailable(self.error or "el agente no ha terminado de arrancar")
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self._run(question, history), self._loop))

    async def _run(self, question: str, history: Sequence[Turn]) -> AgentResult:
        assert self._agent is not None and self._lock is not None
        async with self._lock:
            return await self._agent.run(question, history)


def _data_api_status() -> dict[str, Any]:
    base = env_value("API_BASE_URL", "http://127.0.0.1:8000")
    try:
        res = httpx.get(f"{base}/health", timeout=2.0)
        body = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
        ok = res.status_code == 200 and body.get("status") == "ok"
        return {"reachable": ok, "detail": None if ok else f"HTTP {res.status_code}"}
    except httpx.HTTPError as exc:
        return {"reachable": False, "detail": f"no responde ({type(exc).__name__})"}


def create_chat_app(runner: AgentRunner | None = None,
                    data_api_status: Callable[[], dict[str, Any]] = _data_api_status,
                    memory: ConversationMemory | None = None) -> FastAPI:
    agent_runner = runner or AgentRunner()
    conversations = memory if memory is not None else ConversationMemory()
    max_turns = int(env_value("HISTORY_MAX_TURNS", str(DEFAULT_MAX_TURNS)))
    max_chars = int(env_value("HISTORY_MAX_CHARS", str(DEFAULT_MAX_CHARS)))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        agent_runner.start()
        yield
        agent_runner.stop()

    app = FastAPI(title="Futsal Chat API", version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in env_value("CHAT_CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return JSONResponse({"detail": f"parámetros inválidos: {detail}"}, status_code=400)

    @app.get("/chat/health")
    def health() -> dict[str, Any]:
        provider = env_value("LLM_PROVIDER", "anthropic").lower()
        key_var = KEY_VARS.get(provider)
        llm = {"provider": provider, "key_configured": bool(key_var and env_value(key_var))}
        agent = {"ready": agent_runner.ready, "error": agent_runner.error}
        data = data_api_status()
        ok = agent["ready"] and llm["key_configured"] and data["reachable"]
        return {"status": "ok" if ok else "degraded", "agent": agent, "llm": llm, "data_api": data}

    @app.post("/chat")
    async def chat(body: ChatRequest) -> ChatResponse:
        question = body.question.strip()
        if not question:
            raise HTTPException(400, "la pregunta está vacía")
        cid = body.conversation_id
        known = conversations.get(cid) if cid else None
        stored = known if known is not None else (  # el servidor manda; el navegador solo siembra un id desconocido
            [t for t in turns_from_client((h.role, h.content) for h in body.history) if t.answer != OFF_TOPIC_MESSAGE] if cid else [])
        context = select_turns(stored, max_turns, max_chars)
        try:
            result = await agent_runner.ask(question, context)
        except ChatUnavailable as exc:
            raise HTTPException(503, f"agente no disponible: {exc}") from exc
        if result.error and not result.answer:  # fallo sin respuesta utilizable (LLM caído, cuota…)
            raise HTTPException(502, f"error del agente: {result.error}")
        if (cid and not result.error and result.answer.strip() not in ("", OFF_TOPIC_MESSAGE)
                and result.guardrail in MEMORY_GUARDRAILS):
            conversations.put(cid, [*stored, Turn(question, result.answer)])  # los fallos o respuestas sustituidas no son contexto
        elif cid and known is None and stored:
            conversations.put(cid, stored)
        return build_response(result, len(context))

    @app.delete("/chat/conversations/{conversation_id}")
    def forget(conversation_id: Annotated[str, Path(pattern=CONVERSATION_ID.pattern)]) -> dict[str, bool]:
        return {"deleted": conversations.forget(conversation_id)}

    return app


def app_factory() -> FastAPI:
    """Para `uvicorn futsal.agent.web:app_factory --factory`."""
    return create_chat_app()
