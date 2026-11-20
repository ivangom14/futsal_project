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


class SportScopedMcp:
    """Inyecta el deporte activo en cada herramienta que lo declara y lo oculta al LLM (Fase 1 multideporte).

    El deporte NO lo decide el modelo: lo fija la petición (`/chat` → `Agent.run(sport=…)`) y se aplica de forma determinista a
    todas las llamadas, también a las del contexto precargado y el prefetch. Las herramientas sin parámetro `sport` (servidores
    simulados de los tests) se llaman igual que antes. Una herramienta que exige deporte sin deporte activo es un error
    explícito: nunca se supone fútbol sala."""

    def __init__(self, inner: McpClient) -> None:
        self._inner = inner
        self.sport: str | None = None
        self._scoped: set[str] | None = None  # herramientas que declaran `sport`

    async def _scoped_tools(self) -> set[str]:
        if self._scoped is None:
            await self.list_tools()
        return self._scoped or set()

    async def list_tools(self) -> list[dict[str, Any]]:
        tools = await self._inner.list_tools()
        self._scoped = {t["name"] for t in tools if "sport" in ((t.get("input_schema") or {}).get("properties") or {})}
        return [{**t, "input_schema": _without_sport(t.get("input_schema") or {})} if t["name"] in self._scoped else t for t in tools]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        if name in await self._scoped_tools():
            if self.sport is None:
                return True, "error_validacion: falta el deporte de la conversación (sport)"
            arguments = {**arguments, "sport": self.sport}  # manda la petición, nunca el LLM
        return await self._inner.call_tool(name, arguments)


def _without_sport(schema: dict[str, Any]) -> dict[str, Any]:
    props = {k: v for k, v in (schema.get("properties") or {}).items() if k != "sport"}
    out = {**schema, "properties": props}
    if "required" in schema:
        req = [r for r in schema["required"] if r != "sport"]
        if req:
            out["required"] = req
        else:
            out.pop("required")
    return out


def stdio_params() -> StdioServerParameters:
    """Lanza el servidor MCP de Fase 6 como subproceso stdio."""
    return StdioServerParameters(command=sys.executable,
                                 args=["-m", "futsal.mcp_server.server"], env=dict(os.environ))
