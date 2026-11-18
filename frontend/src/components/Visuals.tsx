import { useState } from "react";
import { ChevronDown } from "lucide-react";
import TeamCrest from "../ui/TeamCrest";
import Panel, { PanelAction, PanelNote } from "../ui/Panel";
import Cards from "./Cards";
import Lineup from "./Lineup";
import Matches from "./Matches";
import Officials from "./Officials";
import Timeline from "./Timeline";
import type { Visual } from "../types";

const fmt = (v: unknown) => (v === null || v === undefined ? "–" : String(v));
/** Filas de ranking visibles de entrada; el resto (ya recibido) se despliega. */
export const INITIAL_RANK_ROWS = 10;
const PARTIAL = "Datos incompletos en la fuente: puede faltar información.";

const partialNote = (partial?: boolean) => (partial ? <p className="mt-0.5 text-xs text-warning">{PARTIAL}</p> : null);

function TableVisual({ v }: { v: Extract<Visual, { type: "table" }> }) {
  const keys = v.columns.map((c) => c.key);
  if (keys.includes("position") && keys.includes("team")) return <StandingsTable v={v} />;
  return (
    <Panel title={v.title} subtitle={v.subtitle} note={partialNote(v.partial)}>
      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={`${v.title} (tabla desplazable)`}>
        <table className="data-table w-full text-sm">
          <thead>
            <tr>
              {v.columns.map((c) => (
                <th key={c.key} scope="col" className={c.align === "right" ? "text-right" : "text-left"}>{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {v.rows.map((r, i) => (
              <tr key={i} className={r.highlight ? "is-highlight" : ""}>
                {v.columns.map((c) => (
                  <td key={c.key} className={c.align === "right" ? "text-right font-display stretch-table tabular-nums" : "text-left"}>{fmt(r[c.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

/** Columnas de una clasificación (contrato de `presentation.py`): con `position` y `team` la tabla se trata como clasificación. */
const STANDINGS_TITLES: Record<string, string> = {
  position: "Posición", played: "Partidos jugados", won: "Ganados", drawn: "Empatados", lost: "Perdidos",
  goals_for: "Goles a favor", goals_against: "Goles en contra", goal_difference: "Diferencia de goles", points: "Puntos",
};
/** Columnas secundarias: se pliegan en tarjetas estrechas (siguen en el DOM y se ven con «Ver todas las columnas», con desplazamiento propio). */
const OPTIONAL: Record<string, string> = {
  goal_difference: "st-opt0", won: "st-opt1", drawn: "st-opt1", lost: "st-opt1", goals_for: "st-opt2", goals_against: "st-opt2",
};
/** A partir de cuántos caracteres un nombre puede no caber en dos líneas: se ofrece verlo entero con un toque. */
export const LONG_TEAM = 34;

function TeamCell({ name }: { name: string }) {
  const [open, setOpen] = useState(false);
  // Nombre oficial completo como texto normal (con su letra de categoría, «… 'B'»), en dos líneas como mucho; un toque lo despliega.
  const label = <span className="st-name" data-team-name="">{name}</span>;
  return (
    <span className="flex min-w-0 items-center gap-2" title={name}>
      <TeamCrest name={name} size={20} />
      {name.length > LONG_TEAM ? (
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label={`${name} (ver nombre completo)`}
          className={`st-name-btn min-w-0 text-left ${open ? "is-open" : ""}`}>{label}</button>
      ) : label}
    </span>
  );
}

/**
 * Clasificación. Prioridad de lectura: posición, escudo y equipo, partidos jugados y puntos; DG, G/E/P y GF/GC se pliegan por consulta de
 * contenedor (< 24, 30 y 38 rem) sin perder el dato: «Ver todas las columnas» las muestra con desplazamiento horizontal INTERNO y «Pos»/«Equipo»
 * fijas. Tabla de anchos fijos: el nombre se queda con todo el espacio libre, sin partir palabras, en dos líneas como mucho (toque = nombre entero).
 * Orden, posiciones y empates: los de la fuente, sin reordenar ni recalcular.
 */
function StandingsTable({ v }: { v: Extract<Visual, { type: "table" }> }) {
  const [all, setAll] = useState(false);
  const cell = (key: string) => {
    if (key === "position") return "st-pos";
    if (key === "team") return "st-team";
    return `${OPTIONAL[key] ?? ""} st-num`;
  };
  const hasOptional = v.columns.some((c) => OPTIONAL[c.key]);
  return (
    <Panel title={v.title} subtitle={v.subtitle} note={partialNote(v.partial)} aside={<span className="tabular-nums">{v.rows.length} equipos</span>}>
      <div className="standings" data-all={all ? "" : undefined} data-testid="standings">
      <div className="st-scroll overflow-x-auto" tabIndex={0} role="region" aria-label="Clasificación (tabla desplazable)">
        <table className="text-sm">
          <thead>
            <tr>
              {v.columns.map((c) => (
                <th key={c.key} scope="col" className={cell(c.key)}>
                  {STANDINGS_TITLES[c.key] ? <abbr title={STANDINGS_TITLES[c.key]} className="no-underline">{c.label}</abbr> : c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {v.rows.map((r, i) => (
              <tr key={i} className={r.highlight ? "is-highlight" : ""} aria-current={r.highlight ? "true" : undefined}>
                {v.columns.map((c) => (
                  <td key={c.key} className={`${cell(c.key)} ${c.key === "points" ? "st-pts" : ""}`}>
                    {c.key === "team" ? <TeamCell name={String(r.team ?? "")} />
                      : c.key === "goal_difference" && typeof r[c.key] === "number" && (r[c.key] as number) > 0 ? `+${r[c.key]}` : fmt(r[c.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hasOptional && (
        <button type="button" onClick={() => setAll((x) => !x)} aria-pressed={all}
          className="st-toggle flex min-h-touch w-full items-center justify-center border-t border-line text-sm font-semibold text-accent hover:bg-surface-2">
          {all ? "Ver menos columnas" : "Ver todas las columnas"}
        </button>
      )}
      </div>
    </Panel>
  );
}

/**
 * Ranking (goleadores, equipos, árbitros): posición, nombre, equipo y la cifra principal, con barra fina de proporción. Los empates
 * repiten posición (la del backend); los 10 primeros de entrada y el resto con un toque; si el backend pagina, «Ver más» pide la
 * página siguiente sin LLM. Por contrato (`presentation.py`), `sublabel` es el equipo del jugador (o la competición).
 */
function BarsVisual({ v, onMore, moreDisabled }: { v: Extract<Visual, { type: "bars" }>; onMore?: () => void; moreDisabled?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const max = Math.max(1, ...v.rows.map((r) => r.value));
  const rows = expanded ? v.rows : v.rows.slice(0, INITIAL_RANK_ROWS);
  const hidden = v.rows.length - rows.length;
  const footer = hidden > 0 ? (
    <PanelAction onClick={() => setExpanded(true)} testId="ranking-expand"><ChevronDown size={16} aria-hidden="true" /> Ver {hidden} más</PanelAction>
  ) : v.partial && onMore ? (
    <PanelAction onClick={onMore} disabled={moreDisabled} testId="ranking-more"><ChevronDown size={16} aria-hidden="true" /> Ver 10 más</PanelAction>
  ) : v.partial && v.subtitle?.startsWith("Mostrando") ? (
    <PanelNote>Pide «los siguientes» para ver más posiciones.</PanelNote>
  ) : v.total && v.total > v.rows.length ? (
    <PanelNote>Se muestran los {v.rows.length} primeros de {v.total}.</PanelNote>
  ) : null;
  return (
    <Panel title={v.title} subtitle={v.subtitle} note={v.subtitle?.startsWith("Mostrando") ? null : partialNote(v.partial)} footer={footer}>
      <ol className="divide-y divide-line">
        {rows.map((r, i) => {
          const rank = r.rank ?? i + 1;
          const tie = i > 0 && (rows[i - 1].rank ?? i) === rank;
          return (
            <li key={i} className="rank-row" data-testid="rank-row">
              <span className={`rank-pos font-display stretch-score tabular-nums ${rank === 1 ? "bg-gold text-gold-text" : tie ? "text-ink-2" : "text-ink"}`}
                aria-label={`Posición ${rank}${tie ? " (empatado)" : ""}`}>{rank}</span>
              <span className="min-w-0">
                <span className="block font-medium leading-tight [overflow-wrap:anywhere]">{r.label}</span>
                {r.sublabel && (
                  <span className="mt-0.5 flex items-center gap-1.5 text-[13px] leading-tight text-ink-2"><TeamCrest name={r.sublabel.split(" · ")[0]} size={14} /><span className="min-w-0 [overflow-wrap:anywhere]">{r.sublabel}</span></span>
                )}
                <span className="rank-bar" aria-hidden="true"><span style={{ width: `${(r.value / max) * 100}%` }} /></span>
              </span>
              <span className="font-display text-lg font-extrabold leading-none stretch-score tabular-nums" aria-label={`${r.value}`}>{r.value}</span>
            </li>
          );
        })}
      </ol>
    </Panel>
  );
}

function ComparisonVisual({ v }: { v: Extract<Visual, { type: "comparison" }> }) {
  return (
    <Panel title={v.title} subtitle={v.subtitle}>
      <div className="space-y-3 p-3">
        <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink">
          <span className="flex items-center gap-1.5"><span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm bg-chart-1" />{v.series[0]}</span>
          <span className="flex items-center gap-1.5"><span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm bg-chart-2" />{v.series[1]}</span>
        </p>
        {v.rows.map((r, i) => {
          const vals = r.values.map((x) => Math.max(0, x ?? 0));
          const max = Math.max(1, ...vals);
          return (
            <div key={i} className="text-sm">
              <p className="mb-0.5 text-xs text-ink-2">{r.label}</p>
              {vals.map((x, j) => (
                <div key={j} className="flex items-center gap-2">
                  <div className="h-2 flex-1 rounded-sm bg-surface-2"><div className={`h-2 rounded-sm ${j === 0 ? "bg-chart-1" : "bg-chart-2"}`} style={{ width: `${(x / max) * 100}%` }} /></div>
                  <span className="w-8 text-right font-display font-bold tabular-nums" aria-label={`${v.series[j]}: ${fmt(r.values[j])}`}>{fmt(r.values[j])}</span>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

const RESULT = {
  V: { label: "Victoria", cls: "bg-success text-surface" },
  E: { label: "Empate", cls: "bg-ink-2 text-surface" },
  D: { label: "Derrota", cls: "bg-danger text-surface" },
} as const;

/** Forma reciente: racha de un vistazo (V/E/D con letra, no solo color), puntos sumados y la lista del más reciente al más antiguo. */
function FormVisual({ v }: { v: Extract<Visual, { type: "form" }> }) {
  const last = v.rows[v.rows.length - 1];
  const newestFirst = [...v.rows].reverse();
  return (
    <Panel title={v.title} subtitle={last ? `${last.points} puntos en ${v.rows.length} partidos` : v.subtitle}
      aside={(
        <span className="flex gap-1" role="img" aria-label={`Racha: ${v.rows.map((r) => RESULT[r.result].label).join(", ")}`} data-testid="form-streak">
          {v.rows.map((r, i) => <span key={i} aria-hidden="true" className={`grid h-5 w-5 place-items-center rounded-sm text-[11px] font-bold ${RESULT[r.result].cls}`}>{r.result}</span>)}
        </span>
      )}>
      <ul className="divide-y divide-line text-sm">
        {newestFirst.map((r, i) => (
          <li key={i} className="flex items-center gap-3 px-3 py-2">
            <span className="w-8 shrink-0 text-xs text-ink-2">J{r.round ?? "?"}</span>
            <span className="min-w-0 flex-1 leading-tight">
              <span className="text-xs text-ink-2">{r.home ? "vs " : "en "}</span>
              <span className="[overflow-wrap:anywhere]" title={r.rival}>{r.rival}</span>
            </span>
            <span className="font-display font-bold stretch-score tabular-nums">{r.score}</span>
            <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-sm text-xs font-bold ${RESULT[r.result].cls}`} aria-label={RESULT[r.result].label}>{r.result}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

/**
 * Renderiza visuales ESTRUCTURADOS que llegan del backend (nunca se interpreta texto libre; todo se pinta como texto, sin HTML).
 * `onMore` (solo la última respuesta) pide la página siguiente del listado paginado.
 */
export default function Visuals({ visuals, onMore, moreDisabled }: { visuals: Visual[]; onMore?: () => void; moreDisabled?: boolean }) {
  return (
    <>
      {visuals.map((v, i) => {
        switch (v.type) {
          case "table": return <TableVisual key={i} v={v} />;
          case "bars": return <BarsVisual key={i} v={v} onMore={onMore} moreDisabled={moreDisabled} />;
          case "comparison": return <ComparisonVisual key={i} v={v} />;
          case "form": return <FormVisual key={i} v={v} />;
          case "lineup": return <Lineup key={i} v={v} />;
          case "cards": return <Cards key={i} v={v} />;
          case "officials": return <Officials key={i} v={v} />;
          case "timeline": return <Timeline key={i} v={v} />;
          case "matches": return <Matches key={i} v={v} onMore={onMore} moreDisabled={moreDisabled} />;
          default: return null; // tipo desconocido: se ignora, la respuesta de texto sigue siendo la alternativa
        }
      })}
    </>
  );
}
