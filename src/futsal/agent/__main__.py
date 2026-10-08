"""CLI: `python -m futsal.agent "pregunta"` (lanza el MCP por stdio; requiere la API)."""

import argparse
import asyncio

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from futsal.agent.agent import DEFAULT_MAX_TOOL_CALLS, Agent
from futsal.agent.llm import LLMError, create_llm
from futsal.agent.mcp_client import SessionMcpClient, stdio_params
from futsal.config.settings import env_value


async def _main(questions: list[str]) -> int:
    llm = create_llm()
    limit = int(env_value("MAX_TOOL_CALLS", str(DEFAULT_MAX_TOOL_CALLS)))
    status = 0
    async with stdio_client(stdio_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            agent = Agent(llm, SessionMcpClient(session), limit,
                          on_trace=lambda line: print(line + "\n"))
            for q in questions:
                res = await agent.run(q)
                status |= 1 if res.error else 0
                print("-" * 40)
    print(f"tokens: {getattr(llm, 'usage', {})}")
    return status


def main() -> None:
    parser = argparse.ArgumentParser(prog="futsal.agent")
    parser.add_argument("questions", nargs="+")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(_main(args.questions)))
    except LLMError as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
