"""Parámetro de deporte de las herramientas MCP (Fase 1 multideporte).

Obligatorio en todas las herramientas de datos: el agente lo inyecta de forma determinista con el deporte de la conversación y
no se lo muestra al LLM (`agent/mcp_client.SportScopedMcp`). `all` solo para consultas combinadas intencionadas."""

from typing import Annotated, Literal

from pydantic import Field

SportArg = Annotated[Literal["futsal", "football11", "all"], Field(
    description="Deporte: futsal (fútbol sala), football11 (fútbol) o all (ambos, solo si se piden los dos)")]
