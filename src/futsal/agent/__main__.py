"""CLI: `python -m futsal.agent "pregunta"` (lanza el MCP por stdio; requiere la API)."""

import argparse
import asyncio
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from futsal.agent.agent import DEFAULT_MAX_ROWS, DEFAULT_MAX_TOOL_CALLS, Agent, AgentResult
from futsal.agent.llm import LLMError, create_llm
from futsal.agent.mcp_client import SessionMcpClient, stdio_params
from futsal.config.settings import env_value

DEFAULT_TRACE_FILE = "data/agent/traces.jsonl"


def _print_run(res: AgentResult) -> None:
    """Resumen de UNA ejecución: tokens por llamada y totales sumando cada llamada una vez."""
    rec = res.run
    if rec is None:
        return
    t = rec.totals()
    est = " (estimados)" if t["tokens_estimated"] else ""
    print(f"run {rec.run_id} | modelo {rec.model} | {t['llm_calls']} llamadas LLM, "
          f"{t['tool_calls']} tools | {t['latency_ms']} ms (LLM {t['llm_ms']}, tools {t['tool_ms']})")
    for c in rec.llm_calls:
        print(f"  llamada {c.index}: in={c.input_tokens} out={c.output_tokens} "
              f"thinking={c.thinking_tokens} | {c.latency_ms} ms")
    print(f"  TOTAL ejecución{est}: in={t['input_tokens']} (máx. una llamada {t['peak_input_tokens']}) "
          f"out={t['output_tokens']} thinking={t['thinking_tokens']}")


async def _main(questions: list[str], trace_path: Path | None, trace_results: bool) -> int:
    llm = create_llm()
    limit = int(env_value("MAX_TOOL_CALLS", str(DEFAULT_MAX_TOOL_CALLS)))
    rows = int(env_value("MAX_RESULT_ROWS", str(DEFAULT_MAX_ROWS)))
    status = 0
    async with stdio_client(stdio_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            agent = Agent(llm, SessionMcpClient(session), limit,
                          on_trace=lambda line: print(line + "\n"), max_rows=rows,
                          trace_path=trace_path, trace_results=trace_results)
            for q in questions:
                res = await agent.run(q)
                status |= 1 if res.error else 0
                _print_run(res)
                print("-" * 40)
    print(f"acumulado de la sesión (todas las preguntas y llamadas): {getattr(llm, 'usage', {})}")
    return status


def main() -> None:
    parser = argparse.ArgumentParser(prog="futsal.agent")
    parser.add_argument("questions", nargs="+")
    parser.add_argument("--trace-file", default=env_value("AGENT_TRACE_FILE", DEFAULT_TRACE_FILE),
                        help="JSONL de trazas (vacío para desactivar)")
    parser.add_argument("--trace-results", action="store_true",
                        help="guardar también los resultados de las tools enviados al LLM")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(_main(args.questions, Path(args.trace_file) if args.trace_file else None,
                                           args.trace_results)))
    except LLMError as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
