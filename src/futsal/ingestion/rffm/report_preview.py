"""Vista previa TXT legible del acta normalizada. Pura: no accede a red ni a BD."""

from futsal.ingestion.rffm.report_models import MatchReport

NA = "No disponible en la fuente"
_LINEUP = {"starter": "titular", "substitute": "suplente", "unknown": "sin dato"}
_STAFF = {"head_coach": "entrenador", "assistant_coach": "segundo entrenador",
          "delegate": "delegado", "field_delegate": "delegado de campo"}


def _v(value: object) -> str:
    return NA if value is None or value == "" else str(value)


def render_preview(report: MatchReport, plan_rows: list[tuple[str, int, int, int]] | None = None) -> str:
    m, r = report.match, report.report
    team = {t.side: t for t in report.teams}
    name = {s: (team[s].name if s in team else None) for s in ("home", "away")}
    score = NA if m.home_score is None else f"{m.home_score}-{m.away_score}"
    out = ["ACTA",
           f"Partido: {_v(r.match_external_id)}  (acta {_v(r.report_external_id)})",
           f"Jornada: {_v(r.round_external_id)}",
           f"Fecha: {_v(m.scheduled_date)} {m.scheduled_time or ''}".rstrip(),
           f"Local: {_v(name['home'])}", f"Visitante: {_v(name['away'])}",
           f"Marcador: {score}", f"URL: {report.source.url}", ""]
    for side, title in (("home", "ALINEACIÓN LOCAL"), ("away", "ALINEACIÓN VISITANTE")):
        out += [title, "Dorsal | Jugador | Rol | Titular/suplente"]
        rows = [p for p in report.players if p.team_side == side]
        out += [f"{_v(p.shirt_number)} | {p.display_name}{' (C)' if p.captain else ''} | {p.role} | "
                f"{_LINEUP[p.lineup_status]}" for p in rows] or [NA]
        out.append("")
    out += ["CUERPO TÉCNICO", "Equipo | Nombre | Rol"]
    out += [f"{_v(name.get(s.team_side) if s.team_side else None) if s.team_side else '(partido)'} | "
            f"{s.display_name} | {_STAFF.get(s.role, s.role)}" for s in report.staff] or [NA]
    out += ["", "OFICIALES", "Nombre | Rol"]
    out += [f"{o.display_name} | {o.role}" for o in report.officials] or [NA]
    out += ["", "EVENTOS", "Minuto | Tipo | Equipo | Jugador | Descripción"]
    for e in report.events:
        detail = f"código={_v(e.source_code)}" + (
            f" segunda_amarilla={e.source_detail.get('segunda_amarilla')}" if e.source_detail else "")
        out.append(f"{_v(e.minute)} | {e.event_type} | {_v(name[e.team_side])} | "
                   f"{_v(e.player_name)} | {detail}")
    if not report.events:
        out.append(NA)
    out += ["", "COMPARACIÓN", "Campo | Resultados | Acta | Coincide"]
    rows_c = report.validation.comparison
    out += [f"{c.field} | {_v(c.matches_value)} | {_v(c.report_value)} | {'sí' if c.equal else 'NO'}"
            for c in rows_c] if rows_c else [NA]
    out += ["", "IMPORTACIÓN PREVISTA", "Tabla | Registros nuevos | Actualizados | Sin cambios"]
    out += [f"{t} | {n} | {u} | {k}" for t, n, u, k in plan_rows] if plan_rows else [NA]
    if report.validation.unparsed_sections:
        out += ["", "SECCIONES NO INTERPRETADAS (estructura sin observar): "
                + ", ".join(report.validation.unparsed_sections)]
    return "\n".join(out) + "\n"
