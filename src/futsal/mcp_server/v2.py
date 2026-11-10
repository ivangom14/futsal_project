"""Servidor MCP v2 (solo lectura), separado del v1: publica con FastMCP el mismo registro de herramientas que usa el agente en proceso.

Arranque: `python -m futsal.mcp_server.v2` (stdio). Usa `DATABASE_URL`.
"""

import sys

from mcp.server.fastmcp import FastMCP

from futsal.agent.v2.registry import V2Registry, default_registry


def create_server(registry: V2Registry | None = None) -> FastMCP:
    reg = registry or default_registry()
    mcp = FastMCP("futsal-v2")
    for spec in reg.specs:
        mcp.add_tool(spec.fn, name=spec.name, description=spec.description)
    return mcp


def main() -> None:
    print("futsal MCP v2 (stdio)", file=sys.stderr)
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
