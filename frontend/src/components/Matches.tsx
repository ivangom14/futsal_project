import { useState } from "react";
import { ChevronDown, TriangleAlert } from "lucide-react";
import { dayHeading, formatKickoff, kickoffLabel, matchStatus, parseScore, type MatchTone } from "../format";
import TeamCrest from "../ui/TeamCrest";
import Panel, { PanelAction, PanelNote } from "../ui/Panel";
import type { MatchesVisual } from "../types";

type Row = MatchesVisual["rows"][number];
/** Filas visibles de entrada; el resto (ya recibido) se despliega con un toque. */
export const INITIAL_ROWS = 10;

/** Estados que se muestran por fila: solo los EXCEPCIONALES (el marcador ya dice «finalizado» y la hora, «programado»). */
const quiet = (status: string) => ["finished", "scheduled"].includes((status ?? "").trim().toLowerCase());

const TONE: Record<MatchTone, string> = {
  neutral: "text-ink-2",
  info: "text-accent",
  warning: "text-warning",
  danger: "text-danger",
};

interface Group { day: string | null; comps: { comp: string | null; rows: Row[] }[] }

/**
 * Agrupa por día (en el orden recibido, que es el cronológico de la API) y, dentro del día, por competición si hay varias: el orden
 * de competiciones es el de su primer partido y dentro de cada una se conserva el orden horario.
 */
export function groupMatches(rows: Row[], byComp: boolean): Group[] {
  const days: Group[] = [];
  for (const r of rows) {
    const day = dayHeading(r.date);
    let g = days.find((d) => d.day === day);
    if (!g) days.push((g = { day, comps: [] }));
    const comp = byComp ? (r.detail ?? null) : null;
    let c = g.comps.find((x) => x.comp === comp);
    if (!c) g.comps.push((c = { comp, rows: [] }));
    c.rows.push(r);
  }
  return days;
}

function srLine(m: Row, showDate: boolean): string {
  const k = formatKickoff(m.date);
  const when = showDate ? kickoffLabel(m.date) : (k?.time ?? "hora sin confirmar");
  const s = parseScore(m.score);
  const teams = s ? `${m.home} ${s[0]}, ${m.away} ${s[1]}` : m.score ? `${m.home} contra ${m.away}, ${m.score}` : `${m.home} contra ${m.away}`;
  return `${when}. ${teams}. ${matchStatus(m.status).label}${m.detail ? `. ${m.detail}` : ""}.`;
}

function Team({ name, goals, win, lose }: { name: string; goals?: number; win: boolean; lose: boolean }) {
  return (
    <>
      <span className="flex min-w-0 items-center gap-2">
        <TeamCrest name={name} size={20} />
        <span className={`match-team ${win ? "font-semibold text-ink" : lose ? "text-ink-2" : "text-ink"}`} title={name}>{name}</span>
      </span>
      <span className={`match-goals font-display stretch-score tabular-nums ${win ? "font-extrabold text-ink" : lose ? "font-semibold text-ink-2" : "font-bold text-ink"}`}>
        {goals ?? ""}
      </span>
    </>
  );
}

function MatchRow({ m, showDate, showRound, showComp }: { m: Row; showDate: boolean; showRound: boolean; showComp: boolean }) {
  const k = formatKickoff(m.date);
  const s = parseScore(m.score);
  const st = matchStatus(m.status);
  const [hg, ag] = s ?? [undefined, undefined];
  const homeWin = s ? s[0] > s[1] : false;
  const awayWin = s ? s[1] > s[0] : false;
  return (
    <li className="match" data-testid="match-row">
      <span className="sr-only">{srLine(m, showDate)}</span>
      <div className="match-grid" aria-hidden="true">
        <div className="match-when" data-testid="match-when">
          {showDate && <span className="block">{k ? k.date : m.date ? m.date : "Sin fecha"}</span>}
          <span className={`block font-display stretch-table text-[13px] font-semibold tabular-nums ${showDate ? "text-ink-2" : "text-ink"}`}>
            {k?.time ?? (showDate ? "" : "—")}
          </span>
          {showRound && m.round !== null && <span className="block">J{m.round}</span>}
          {!quiet(m.status) && <span className={`mt-0.5 block font-semibold ${TONE[st.tone]}`} data-testid="match-status"
            title={st.label === "Sin confirmar" ? `Estado en la fuente: ${m.status || "vacío"}` : undefined}>{st.label}</span>}
          {quiet(m.status) && m.status.trim().toLowerCase() === "finished" && !s && <span className="mt-0.5 block font-semibold text-ink-2" data-testid="match-status">Finalizado</span>}
        </div>
        <div className="match-teams">
          <Team name={m.home} goals={hg} win={homeWin} lose={awayWin} />
          <Team name={m.away} goals={ag} win={awayWin} lose={homeWin} />
          {showComp && m.detail && <span className="match-detail">{m.detail}</span>}
        </div>
        {m.score && !s && <span className="match-raw font-display font-bold tabular-nums" data-testid="match-score">{m.score}</span>}
      </div>
      {m.past_due && (
        <p className="match-note flex items-center gap-1 text-xs text-warning">
          <TriangleAlert size={13} aria-hidden="true" className="shrink-0" /> La fecha ya pasó y sigue como programado: el dato puede estar desactualizado.
        </p>
      )}
    </li>
  );
}

const ALL_LABEL: Record<string, string> = { finished: "finalizados", scheduled: "programados" };

/**
 * Partidos (resultados o próximos encuentros), con densidad de app deportiva: una fila por partido con la hora a la izquierda, los
 * dos equipos apilados con su escudo y el marcador alineado a la derecha (el ganador en negrita). Agrupados por día cuando hay
 * varios partidos el mismo día, y por competición si se mezclan. Lo que comparten todas las filas (estado, jornada, competición) se
 * dice UNA vez en la cabecera. Nombres completos: saltan de línea, nunca se cortan. Fecha y hora en Europe/Madrid.
 */
export default function Matches({ v, onMore, moreDisabled }: { v: MatchesVisual; onMore?: () => void; moreDisabled?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const rows = v.rows;
  const comps = new Set(rows.map((r) => r.detail ?? null));
  const rounds = new Set(rows.map((r) => r.round));
  const statuses = new Set(rows.map((r) => (r.status ?? "").trim().toLowerCase()));
  const days = groupMatches(rows, false);
  const byDay = days.some((d) => d.comps[0].rows.length > 1) && days.every((d) => d.day !== null);
  const byComp = comps.size > 1;
  const sameRound = rounds.size === 1 && rows[0]?.round != null ? rows[0].round : null;
  const visible = expanded ? rows : rows.slice(0, INITIAL_ROWS);
  const hidden = rows.length - visible.length;
  const shared = [
    sameRound !== null ? `Jornada ${sameRound}` : null,
    !byComp && comps.size === 1 && rows[0]?.detail ? rows[0].detail : null,
    statuses.size === 1 && ALL_LABEL[[...statuses][0]] ? `Todos ${ALL_LABEL[[...statuses][0]]}` : null,
  ].filter(Boolean).join(" · ");
  const count = v.total > rows.length ? `${rows.length} de ${v.total}` : `${rows.length} ${rows.length === 1 ? "partido" : "partidos"}`;
  const left = v.total - rows.length;
  const footer = hidden > 0 ? (
    <PanelAction onClick={() => setExpanded(true)} testId="matches-expand"><ChevronDown size={16} aria-hidden="true" /> Ver {hidden} {hidden === 1 ? "partido más" : "partidos más"}</PanelAction>
  ) : left > 0 && onMore ? (
    <PanelAction onClick={onMore} disabled={moreDisabled} testId="matches-more"><ChevronDown size={16} aria-hidden="true" /> Ver {Math.min(10, left)} partidos más · quedan {left}</PanelAction>
  ) : v.partial ? (
    <PanelNote>Se muestran {rows.length} de {v.total} partidos. Pide «los siguientes» o acota por jornada, día o equipo.</PanelNote>
  ) : null;
  const scores = rows.some((r) => parseScore(r.score));
  const list = (items: Row[], showDate: boolean) => (
    <ul className={`match-list divide-y divide-line ${showDate ? "with-date" : ""} ${scores ? "" : "no-scores"}`}>
      {items.map((m, i) => <MatchRow key={i} m={m} showDate={showDate} showRound={showDate && sameRound === null} showComp={showDate && byComp} />)}
    </ul>
  );
  const shown = new Set(visible);
  return (
    <Panel title={v.title} subtitle={[v.subtitle, shared].filter(Boolean).join(" · ") || null} aside={v.subtitle?.startsWith("Mostrando") ? undefined : <span className="tabular-nums" data-testid="matches-count">{count}</span>} footer={footer}>
      {byDay ? (
        groupMatches(visible, byComp).map((d) => (
          <section key={d.day} aria-label={d.day ?? "Sin fecha"}>
            <h3 className="match-day">{d.day}</h3>
            {d.comps.map((c) => (
              <div key={c.comp ?? "-"}>
                {byComp && <h4 className="match-comp">{c.comp ?? "Otra competición"}</h4>}
                {list(c.rows.filter((r) => shown.has(r)), false)}
              </div>
            ))}
          </section>
        ))
      ) : (
        list(visible, true)
      )}
    </Panel>
  );
}
