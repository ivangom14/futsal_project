"""Piezas simuladas compartidas por las pruebas del agente: MCP falso con un catálogo de 4 grupos y un LLM con guion."""

import json
from typing import Any

from futsal.agent.llm import LLMResponse, Message, ToolCall

GROUPS = {  # competición -> [(id de grupo, nombre de grupo, equipos)]
    "TERCERA DIVISIÓN FS": [(6, "Grupo 3", ["C.D. DOSA", "PARQUE NORTE F.S. - RODILLITO 'B'", "F.S. DIAGONAL 'A'", "A. D. OBISPO PERELLO 'B'"]),
                            (7, "Grupo 4", ["F.S. DIAGONAL 'A'", "C.D. LOPE DE VEGA 'B'"])],
    "COPA SENIOR I - fase final- FS": [(8, "Eliminatorias", ["C.D. DOSA"])],
    "PRIMERA JUVENIL FS": [(9, "Grupo 1", ["TORREJON SALA FIVE PLAY 'B'"])],
}
TEAM_IDS = {("C.D. DOSA", 6): 11, ("PARQUE NORTE F.S. - RODILLITO 'B'", 6): 12, ("F.S. DIAGONAL 'A'", 6): 13, ("A. D. OBISPO PERELLO 'B'", 6): 14,
            ("F.S. DIAGONAL 'A'", 7): 21, ("C.D. LOPE DE VEGA 'B'", 7): 22, ("C.D. DOSA", 8): 31, ("TORREJON SALA FIVE PLAY 'B'", 9): 41}


class FakeMcp:
    def __init__(self, fail_find: bool = False, wrong_find: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_find, self.wrong_find = fail_find, wrong_find

    async def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}}
                for n in ("list_competitions", "find_teams", "list_matches", "list_teams", "list_rounds", "get_standings")]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        self.calls.append((name, dict(arguments)))
        if name == "list_competitions":
            return False, json.dumps({"competitions": [{"id": i, "name": c, "groups": [{"id": g, "name": gn} for g, gn, _ in gs]}
                                                       for i, (c, gs) in enumerate(GROUPS.items(), 1)], "count": len(GROUPS)})
        if name == "list_teams":
            gid = arguments["group_id"]
            return False, json.dumps({"teams": [{"id": TEAM_IDS[(t, gid)], "name": t} for _, (c, gs) in enumerate(GROUPS.items()) for g, _, ts in gs if g == gid for t in ts],
                                      "count": 1})
        if name == "find_teams":
            if self.fail_find:
                return True, "error_dependencia: API no disponible"
            needle = arguments["name"].lower()
            rows = [{"id": TEAM_IDS[(t, g)], "name": t, "group_id": g, "group": gn, "competition_id": 1, "competition": c}
                    for c, gs in GROUPS.items() for g, gn, ts in gs for t in ts if needle in t.lower()]
            if self.wrong_find:
                rows = [{"id": 99, "name": "OTRO EQUIPO", "group_id": 6, "group": "Grupo 3", "competition_id": 1, "competition": "TERCERA DIVISIÓN FS"}]
            return False, json.dumps({"teams": rows, "count": len(rows)})
        if name == "list_matches":
            return False, json.dumps({"matches": [{"id": 1, "home": "C.D. DOSA", "away": "X", "home_score": 6, "away_score": 1, "status": "finished"}], "count": 1})
        return False, json.dumps({"ok": True})


class Script:
    provider, model = "fake", "fake-model"

    def __init__(self, steps: list[LLMResponse]) -> None:
        self.steps = list(steps)
        self.seen: list[tuple[str, list[Message]]] = []

    def complete(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        self.seen.append((system, [Message(m.role, m.text, list(m.tool_calls), list(m.tool_results)) for m in messages]))
        return self.steps.pop(0)


def call(tool_name: str, **args: Any) -> LLMResponse:
    return LLMResponse("", [ToolCall(f"c-{tool_name}", tool_name, args)])


def final(text: str = "El C.D. DOSA ganó 6-1.") -> LLMResponse:
    return LLMResponse(text, [])
