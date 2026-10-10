"""Prueba de integración REAL con el LLM (de pago). Desactivada por defecto.

Ejecutar solo de forma explícita (API arriba en API_BASE_URL, PostgreSQL con datos y clave en .env):
    set RUN_LIVE_LLM=1
    python -m pytest tests/live -q -m live
Usa como máximo 2 casos para acotar el coste.
"""

import asyncio
import os

import pytest

pytestmark = pytest.mark.live


@pytest.mark.skipif(os.environ.get("RUN_LIVE_LLM") != "1", reason="define RUN_LIVE_LLM=1 para llamar al LLM real")
def test_live_agent_answers_two_cases_with_structured_assertions() -> None:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    from futsal.agent.evaluate import evaluate, load_truth
    from futsal.agent.llm import create_llm
    from futsal.agent.mcp_client import SessionMcpClient, stdio_params
    from futsal.db.session import make_engine

    truth = load_truth(make_engine())
    assert truth is not None, "sin datos en PostgreSQL"

    async def go() -> list[object]:
        async with stdio_client(stdio_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return list(await evaluate(truth, SessionMcpClient(session), "live", create_llm(),
                                           only={"equipo_resultados", "fuera_de_alcance"}))

    results = asyncio.run(go())
    assert results and all(getattr(r, "status") in ("pass", "not_evaluable") for r in results), results
