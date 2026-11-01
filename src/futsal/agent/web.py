"""Servicio HTTP del chat (Fase 22): aloja el agente real para el frontend web.

Solo habla con el LLM y con el servidor MCP (que a su vez usa la API de datos); nunca con PostgreSQL.
`uvicorn futsal.agent.web:app_factory --factory --port 8001`.
"""

import asyncio
import logging
import threading
from pathlib import Path as FilePath
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
from futsal.agent import presentation as pres
from futsal.agent.feedback import FeedbackRequest, RunSummaries, record
from futsal.agent.llm import create_llm
from futsal.agent.messages import FriendlyError, classify_error
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
from futsal.agent.presentation import compute_focus, merge_focus, sanitize_focus
from futsal.agent.trace import RunRecord, redact
from futsal.config.settings import env_value

log = logging.getLogger("futsal.chat")
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
    focus: dict[str, str] = Field(default_factory=dict)  # foco de la conversación (nombres); solo siembra, como `history`


class Source(BaseModel):
    kind: str  # "official_data" (datos de la RFFM vía MCP/PostgreSQL) | "document" (fragmento RAG: reglamento, circulares)
    title: str
    detail: str | None = None
    pages: str | None = None
    text: str | None = None
    cited: bool | None = None  # documentos: ¿la respuesta se apoya en este fragmento? (None = no aplica)
    queried_at: str | None = None  # cuándo se consultó
    updated_at: str | None = None  # cuándo se actualizaron los datos (None = no se conoce; no se inventa)
    freshness: str | None = None  # fresh | stale | known | unknown
    updated_note: str | None = None
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
    resolved_scope: dict[str, Any] | None = None  # ámbito (tipo, categoría, grupo, competiciones) que la aplicación resolvió
    resolved_question: str | None = None  # cómo se interpretó el mensaje al unirlo a una aclaración pendiente
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
    answer_kind: str = "none"  # official | documental | mixed | none: de dónde sale lo que se muestra
    is_analysis: bool = False  # análisis o recomendación del modelo: no es información oficial
    sources: list[Source]
    visuals: list[dict[str, Any]] = Field(default_factory=list)  # tablas/gráficos construidos solo con datos de las tools
    suggestions: list[str] = Field(default_factory=list)
    focus: dict[str, str] = Field(default_factory=dict)  # de qué trata la conversación (nombres)
    diagnostics: Diagnostics


def build_response(result: AgentResult, history_turns: int, question: str, prev_focus: dict[str, str], asked: list[str],
                   freshness: dict[int, dict[str, Any]]) -> ChatResponse:
    rec = result.run or RunRecord()
    totals = rec.totals()
    tools = [ToolInfo(name=t.name, arguments=t.arguments, duration_ms=round(t.duration_ms, 1), is_error=t.is_error,
                      refused=t.refused, summary=t.result_summary) for t in rec.tool_calls]
    errors = [redact(f"herramienta {t.name}: {t.result_summary}") for t in rec.tool_calls if t.is_error]
    errors += [redact(f"llamada LLM {c.index}: {c.error}") for c in rec.llm_calls if c.error]
    diag = Diagnostics(
        run_id=rec.run_id, provider=rec.provider, model=rec.model, duration_ms=round(rec.latency_ms, 1), history_turns=history_turns,
        resolved_question=result.resolved_question, resolved_scope=result.resolved_scope,
        llm_calls=totals["llm_calls"], tool_calls=totals["tool_calls"], input_tokens=totals["input_tokens"],
        output_tokens=totals["output_tokens"], token_source=totals["token_source"], tools=tools, errors=errors)
    clean = not result.error and result.guardrail in MEMORY_GUARDRAILS  # solo una consulta limpia mueve el foco
    focus = merge_focus(prev_focus, compute_focus(rec, result.names, result.groups_used, result.official, question)) if clean else prev_focus
    sources = pres.build_sources(question, result.answer, rec, result.official, result.groups_used, freshness)
    kind, analysis = pres.answer_kind(question, result.answer, rec)
    visuals = pres.build_visuals(question, rec, result.official, result.names) if clean else []
    return ChatResponse(
        answer=result.answer, guardrail=result.guardrail, error=result.error, answer_kind=kind, is_analysis=analysis,
        sources=[Source(**src) for src in sources], visuals=visuals, focus=focus, diagnostics=diag,
        suggestions=pres.build_suggestions(question, result.answer, rec, focus, asked, sources))


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

    async def ask(self, question: str, history: Sequence[Turn] = (), focus: dict[str, Any] | None = None) -> AgentResult:
        if self._agent is None:  # primer arranque fallido o aún en curso: reintenta
            self.start()
            await asyncio.get_running_loop().run_in_executor(None, self._settled.wait, 30)
        if self._agent is None or self._loop is None:
            raise ChatUnavailable(self.error or "el agente no ha terminado de arrancar")
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self._run(question, history, focus or {}), self._loop))

    async def _run(self, question: str, history: Sequence[Turn], focus: dict[str, Any]) -> AgentResult:
        assert self._agent is not None and self._lock is not None
        async with self._lock:
            return await self._agent.run(question, history, focus)


def _data_api_status() -> dict[str, Any]:
    base = env_value("API_BASE_URL", "http://127.0.0.1:8000")
    try:
        res = httpx.get(f"{base}/health", timeout=2.0)
        body = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
        ok = res.status_code == 200 and body.get("status") == "ok"
        return {"reachable": ok, "detail": None if ok else f"HTTP {res.status_code}"}
    except httpx.HTTPError as exc:
        return {"reachable": False, "detail": f"no responde ({type(exc).__name__})"}


UNAVAILABLE = FriendlyError("unavailable", 503, "El servicio no está disponible ahora mismo. Inténtalo de nuevo dentro de unos instantes.")


def _failure(friendly: FriendlyError, technical: str, status: int | None = None) -> JSONResponse:
    """Error para el usuario en lenguaje llano; el detalle técnico (sin secretos) va al log y al campo `technical`."""
    detail = redact(technical)
    log.warning("chat %s (%s): %s", friendly.code, status or friendly.status, detail)
    return JSONResponse({"detail": friendly.message, "code": friendly.code, "technical": detail}, status_code=status or friendly.status)


FRESHNESS_TTL = 60.0
_freshness_cache: dict[int, tuple[float, dict[str, Any] | None]] = {}


def api_freshness(group_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Frescura (Fase 21) de los grupos consultados, desde la API de datos (solo lectura, caché de 60 s). Si falla, no se inventa nada."""
    import time

    base = env_value("API_BASE_URL", "http://127.0.0.1:8000")
    out: dict[int, dict[str, Any]] = {}
    for gid in group_ids[:3]:
        cached = _freshness_cache.get(gid)
        if cached is None or time.monotonic() - cached[0] > FRESHNESS_TTL:
            try:
                res = httpx.get(f"{base}/groups/{gid}/freshness", timeout=2.0)
                cached = (time.monotonic(), res.json() if res.status_code == 200 else None)
            except (httpx.HTTPError, ValueError):
                cached = (time.monotonic(), None)
            _freshness_cache[gid] = cached
        if cached[1]:
            out[gid] = cached[1]
    return out


def create_chat_app(runner: AgentRunner | None = None,
                    data_api_status: Callable[[], dict[str, Any]] = _data_api_status,
                    memory: ConversationMemory | None = None,
                    freshness: Callable[[list[int]], dict[int, dict[str, Any]]] = api_freshness,
                    feedback_path: FilePath | None = None) -> FastAPI:
    agent_runner = runner or AgentRunner()
    conversations = memory if memory is not None else ConversationMemory()
    summaries = RunSummaries()
    feedback_file = feedback_path or FilePath(env_value("FEEDBACK_FILE", "data/feedback/feedback.jsonl"))
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

    @app.post("/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest) -> ChatResponse | JSONResponse:
        question = body.question.strip()
        if not question:
            raise HTTPException(400, "la pregunta está vacía")
        cid = body.conversation_id
        known = conversations.get(cid) if cid else None
        stored = known if known is not None else (  # el servidor manda; el navegador solo siembra un id desconocido
            [t for t in turns_from_client((h.role, h.content) for h in body.history) if t.answer != OFF_TOPIC_MESSAGE] if cid else [])
        context = select_turns(stored, max_turns, max_chars)
        # el servidor manda: solo una conversación que no conoce se siembra con el foco del navegador (sanitizado)
        prev_focus = (conversations.get_focus(cid) or ({} if known is not None else sanitize_focus(body.focus))) if cid else {}
        try:
            result = await agent_runner.ask(question, context, prev_focus)
        except ChatUnavailable as exc:
            return _failure(UNAVAILABLE, str(exc), 503)
        except Exception as exc:  # noqa: BLE001 - nunca un traceback al usuario; el detalle va al log
            log.exception("error inesperado al procesar la consulta")
            return _failure(classify_error(f"{type(exc).__name__}: {exc}"), f"{type(exc).__name__}: {exc}")
        if result.error and not result.answer:  # fallo sin respuesta utilizable (LLM caído, cuota…)
            return _failure(classify_error(result.error), result.error)
        if (cid and not result.error and result.answer.strip() not in ("", OFF_TOPIC_MESSAGE)
                and result.guardrail in MEMORY_GUARDRAILS):
            conversations.put(cid, [*stored, Turn(question, result.answer)])  # los fallos o respuestas sustituidas no son contexto
        elif cid and known is None and stored:
            conversations.put(cid, stored)
        groups = sorted(result.groups_used)
        response = await asyncio.get_running_loop().run_in_executor(
            None, lambda: build_response(result, len(context), question, prev_focus, [t.question for t in stored[-6:]],
                                         freshness(groups) if groups else {}))
        d = response.diagnostics
        summaries.add(d.run_id, {"model": d.model, "provider": d.provider, "tools": [t.name for t in d.tools], "tool_errors": len(d.errors),
                                 "guardrail": response.guardrail, "history_turns": d.history_turns, "duration_ms": d.duration_ms,
                                 "answer_kind": response.answer_kind, "sources": len(response.sources),
                                 "resolved_scope": bool(d.resolved_scope)})
        if cid and response.focus != prev_focus:
            conversations.put_focus(cid, response.focus)
        return response

    @app.post("/chat/feedback")
    def feedback(body: FeedbackRequest) -> dict[str, bool]:
        """Valoración 👍/👎 de una respuesta (motivo y comentario solo con 👎). Se guarda aparte; no altera al agente."""
        record(feedback_file, body, summaries.get(body.run_id))
        return {"saved": True}

    @app.delete("/chat/conversations/{conversation_id}")
    def forget(conversation_id: Annotated[str, Path(pattern=CONVERSATION_ID.pattern)]) -> dict[str, bool]:
        return {"deleted": conversations.forget(conversation_id)}

    return app


def app_factory() -> FastAPI:
    """Para `uvicorn futsal.agent.web:app_factory --factory`."""
    return create_chat_app()
