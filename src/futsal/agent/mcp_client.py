"""Cliente MCP mínimo: descubre y ejecuta tools de un servidor (sesión ya abierta)."""

import os
import sys
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.types import TextContent


class McpClient(Protocol):
    async def list_tools(self) -> list[dict[str, Any]]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """Devuelve (es_error, texto)."""
        ...


def strip_titles(schema: Any) -> Any:
    """Quita los `title` autogenerados del JSON Schema (ruido que el LLM paga en cada llamada)."""
    if isinstance(schema, dict):
        return {k: ({n: strip_titles(v) for n, v in val.items()} if k == "properties" else strip_titles(val))
                for k, val in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [strip_titles(x) for x in schema]
    return schema


class SessionMcpClient:
    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def list_tools(self) -> list[dict[str, Any]]:
        res = await self._session.list_tools()
        return [{"name": t.name, "description": t.description or "",
                 "input_schema": strip_titles(t.inputSchema)} for t in res.tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        res = await self._session.call_tool(name, arguments)
        text = "\n".join(c.text for c in res.content if isinstance(c, TextContent))
        return bool(res.isError), text


def stdio_params() -> StdioServerParameters:
    """Lanza el servidor MCP de Fase 6 como subproceso stdio."""
    return StdioServerParameters(command=sys.executable,
                                 args=["-m", "futsal.mcp_server.server"], env=dict(os.environ))
