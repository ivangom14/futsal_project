"""Bucle de tool calling: LLM client + MCP client, con límite de tool calls y traza."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from futsal.agent.llm import LLMClient, LLMError, Message, ToolCall, ToolResult
from futsal.agent.mcp_client import McpClient

SYSTEM_PROMPT = (
    "Eres un asistente de consulta de fútbol sala RFFM. Dispones de tools para consultar "
    "los datos de la competición: úsalas siempre que necesites información. "
    "No inventes datos; responde en español usando solo los resultados obtenidos. "
    "Si una tool devuelve un error, díselo al usuario."
)
DEFAULT_MAX_TOOL_CALLS = 5


@dataclass
class AgentResult:
    answer: str
    tool_calls: int
    trace: list[str] = field(default_factory=list)
    error: str | None = None


def summarize(is_error: bool, text: str, limit: int = 120) -> str:
    """Resumen de una línea de un resultado MCP para la traza."""
    if is_error:
        return f"ERROR {text[:limit]}"
    try:
        data = json.loads(text)
    except ValueError:
        return text[:limit]
    if isinstance(data, dict) and "count" in data:
        key = next((k for k in data if k != "count"), "items")
        return f"{data['count']} {key}"
    return f"{text[:limit]}{'…' if len(text) > limit else ''}"


class Agent:
    def __init__(self, llm: LLMClient, mcp: McpClient,
                 max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
                 on_trace: Callable[[str], None] | None = None) -> None:
        self._llm = llm
        self._mcp = mcp
        self._max = max_tool_calls
        self._on_trace = on_trace
        self._tools: list[dict[str, Any]] | None = None

    async def run(self, question: str) -> AgentResult:
        result = AgentResult("", 0)

        def log(line: str) -> None:
            result.trace.append(line)
            if self._on_trace:
                self._on_trace(line)

        if self._tools is None:
            self._tools = await self._mcp.list_tools()
        messages = [Message("user", question)]
        log(f"USER\n{question}")
        while True:
            try:
                resp = self._llm.complete(SYSTEM_PROMPT, messages, self._tools)
            except LLMError as exc:
                result.error = str(exc)
                log(f"ERROR\n{exc}")
                return result
            if not resp.tool_calls:
                result.answer = resp.text
                log("LLM\n→ final answer")
                log(f"ASSISTANT\n{resp.text}")
                return result
            if result.tool_calls + len(resp.tool_calls) > self._max:
                result.error = f"límite de {self._max} tool calls alcanzado"
                log(f"ERROR\n{result.error}; se detiene el ciclo sin ejecutar más tools")
                return result
            messages.append(Message("assistant", resp.text, resp.tool_calls))
            results = [await self._execute(c, result, log) for c in resp.tool_calls]
            messages.append(Message("tool", tool_results=results))

    async def _execute(self, call: ToolCall, result: AgentResult,
                       log: Callable[[str], None]) -> ToolResult:
        result.tool_calls += 1
        log(f"LLM\n→ tool: {call.name}\n→ arguments: {json.dumps(call.arguments)}")
        try:
            is_error, text = await self._mcp.call_tool(call.name, call.arguments)
        except Exception as exc:  # el error vuelve al LLM, no rompe el ciclo
            is_error, text = True, f"error_dependencia: {type(exc).__name__}"
        log(f"MCP\n→ {summarize(is_error, text)}")
        return ToolResult(call.id, text, is_error)
