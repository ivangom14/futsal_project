"""Vista previa TXT de la importación (sin base de datos, sin escritura de datos)."""

import re
from pathlib import Path

from futsal.importer.schema import read_league
from futsal.importer.transform import SOURCE, ImportPlan, MatchRec, build_plan

_SECRET = re.compile(
    r"(?i)(password|passwd|pwd|token|secret|api[_-]?key|cookie|authorization)\s*[=:]\s*\S+")
_URL_CRED = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")


def sanitize(text: str) -> str:
    text = _URL_CRED.sub(r"\1***:***@", text)
    return _SECRET.sub(lambda m: f"{m.group(1)}=***", text)


def _cell(v: object) -> str:
    return "null" if v is None else str(v)


def _table(title: str, header: list[str], rows: list[list[object]]) -> list[str]:
    cells = [header] + [[_cell(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(header))]

    def line(r: list[str]) -> str:
        return " | ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip()

    return [f"TABLA {title}", "-" * (len(title) + 6), *(line(r) for r in cells), ""]


def _score(m: MatchRec) -> str | None:
    return None if m.home_score is None else f"{m.home_score}-{m.away_score}"


def _samples(plan: ImportPlan) -> list[MatchRec]:
    picks: list[MatchRec] = []
    for status in ("finished", "scheduled"):
        found = next((m for m in plan.matches if m.status == status), None)
        if found:
            picks.append(found)
    if plan.matches and plan.matches[-1] not in picks:
        picks.append(plan.matches[-1])
    return picks


def render_preview(plan: ImportPlan) -> str:
    rounds = {r.external_id: r for r in plan.rounds}
    names = {t.external_id: t.name for t in plan.teams}
    n = 3
    out = ["IMPORTACIÓN PREVISTA", "====================", "", "ORIGEN",
           f"Temporada externa: {plan.season_external_id}",
           f"Competición externa: {plan.competition_external_id}",
           f"Grupo externo: {plan.group_external_id}", ""]
    out += _table("seasons", ["external_id", "source", "name"],
                  [[plan.season_external_id, SOURCE, plan.season_name]])
    out += _table("competitions", ["external_id", "season_external_id", "name"],
                  [[plan.competition_external_id, plan.season_external_id, plan.competition_name]])
    out += _table("competition_groups", ["external_id", "competition_external_id", "name"],
                  [[plan.group_external_id, plan.competition_external_id, plan.group_name]])
    out += _table("rounds", ["external_id", "visible_label", "scheduled_date"],
                  [[r.external_id, r.visible_label, r.scheduled_date] for r in plan.rounds[:n]])
    out += _table("teams", ["external_id", "name"], [[t.external_id, t.name] for t in plan.teams[:n]])
    sample = _samples(plan)
    out += _table("matches", ["external_id", "round", "home", "away", "score", "status"],
                  [[m.external_id, rounds[m.round_external_id].visible_label,
                    names[m.home_external_id], names[m.away_external_id], _score(m), m.status]
                   for m in sample])
    out += _table("match_observations", ["match_external_id", "status", "score", "content_hash"],
                  [[m.external_id, m.status, _score(m), m.content_hash[:16] + "…"] for m in sample])
    out += ["RECUENTOS ESPERADOS", "-------------------", "seasons: 1", "competitions: 1",
            "groups: 1", f"rounds: {len(plan.rounds)}", f"teams: {len(plan.teams)}",
            f"matches: {len(plan.matches)}", f"observations iniciales: {len(plan.matches)}",
            f"incidencias de calidad: {len(plan.issues)}", ""]
    return sanitize("\n".join(out))


def write_preview(input_path: Path, output: Path) -> ImportPlan:
    plan = build_plan(read_league(input_path))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_preview(plan), encoding="utf-8")
    return plan


__all__ = ["render_preview", "sanitize", "write_preview"]
