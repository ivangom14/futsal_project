import { useState } from "react";
import TeamCrest from "../ui/TeamCrest";
import Cards from "./Cards";
import Lineup from "./Lineup";
import Matches from "./Matches";
import Officials from "./Officials";
import Timeline from "./Timeline";
import type { Visual } from "../types";

const fmt = (v: unknown) => (v === null || v === undefined ? "–" : String(v));

function Frame({ title, subtitle, partial, children }: { title: string; subtitle?: string | null; partial?: boolean; children: React.ReactNode }) {
  return (
    <figure className="mt-3 overflow-hidden rounded-xl border border-line bg-surface" data-testid="visual">
      <figcaption className="border-b border-line bg-surface-2 px-3 py-2">
        <p className="font-display text-sm font-bold text-ink">{title}</p>
        {subtitle && <p className="text-xs text-ink-2">{subtitle}</p>}
        {partial && <p className="text-xs text-warning">Datos incompletos en la fuente: puede faltar información.</p>}
      </figcaption>
      {children}
    </figure>
  );
}

/** Columnas de una clasificación (contrato de `presentation.py`): con `position` y `team` la tabla se trata como clasificación. */
const STANDINGS_TITLES: Record<string, string> = {
  position: "Posición", played: "Partidos jugados", won: "Ganados", drawn: "Empatados", lost: "Perdidos",
  goals_for: "Goles a favor", goals_against: "Goles en contra", goal_difference: "Diferencia de goles", points: "Puntos",
};
/** Columnas que se pliegan en tarjetas estrechas (siguen en el DOM y se ven con «Ver todas las columnas»). */
const OPTIONAL: Record<string, string> = { won: "st-opt1", drawn: "st-opt1", lost: "st-opt1", goals_for: "st-opt2", goals_against: "st-opt2" };

function TableVisual({ v }: { v: Extract<Visual, { type: "table" }> }) {
  const keys = v.columns.map((c) => c.key);
  if (keys.includes("position") && keys.includes("team")) return <StandingsTable v={v} />;
  return (
    <Frame title={v.title} subtitle={v.subtitle} partial={v.partial}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-ink-2">
              {v.columns.map((c) => (
                <th key={c.key} scope="col" className={`px-2 py-1 font-medium ${c.align === "right" ? "text-right" : "text-left"}`}>{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {v.rows.map((r, i) => (
              <tr key={i} className={`border-t border-line ${r.highlight ? "bg-brand-soft font-semibold" : ""}`}>
                {v.columns.map((c) => (
                  <td key={c.key} className={`px-2 py-1 ${c.align === "right" ? "text-right font-display stretch-table tabular-nums" : "text-left"}`}>{fmt(r[c.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Frame>
  );
}

/**
 * Clasificación (guía §F.6): «Pos» y «Equipo» fijas al desplazar; en tarjetas estrechas se pliegan G/E/P (< 22 rem) y GF/GC
 * (< 34 rem) por consulta de contenedor, sin perder el dato: «Ver todas las columnas» las muestra con desplazamiento horizontal.
 */
function StandingsTable({ v }: { v: Extract<Visual, { type: "table" }> }) {
  const [all, setAll] = useState(false);
  const cell = (key: string, hl: boolean) => {
    if (key === "position") return `sticky left-0 z-[1] w-10 min-w-10 text-center ${hl ? "bg-brand-soft shadow-[inset_3px_0_0_var(--fs-brand)]" : "bg-surface"}`;
    if (key === "team") return `sticky left-10 z-[1] text-left shadow-[1px_0_0_var(--fs-line)] ${hl ? "bg-brand-soft" : "bg-surface"}`;
    return `${OPTIONAL[key] ?? ""} text-right`;
  };
  return (
    <Frame title={v.title} subtitle={v.subtitle} partial={v.partial}>
      <div className="standings overflow-x-auto" data-all={all ? "" : undefined} data-testid="standings">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-ink-2">
              {v.columns.map((c) => (
                <th key={c.key} scope="col" className={`px-2 py-1.5 font-medium ${cell(c.key, false)}`}>
                  {STANDINGS_TITLES[c.key] ? <abbr title={STANDINGS_TITLES[c.key]} className="no-underline">{c.label}</abbr> : c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {v.rows.map((r, i) => {
              const hl = Boolean(r.highlight);
              return (
                <tr key={i} className={`border-t border-line ${hl ? "bg-brand-soft font-semibold" : ""}`}>
                  {v.columns.map((c) => (
                    <td key={c.key} className={`px-2 py-1.5 ${cell(c.key, hl)} ${c.key === "team" ? "" : "font-display stretch-table tabular-nums"} ${c.key === "points" ? "font-extrabold" : ""}`}>
                      {c.key === "team" ? (
                        <span className="flex min-w-[7.5rem] items-center gap-2 leading-tight">
                          <TeamCrest name={String(r.team ?? "")} size={20} />
                          <span className="min-w-0 [overflow-wrap:anywhere]">{fmt(r.team)}</span>
                        </span>
                      ) : c.key === "goal_difference" && typeof r[c.key] === "number" && (r[c.key] as number) > 0 ? `+${r[c.key]}` : fmt(r[c.key])}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
        <button type="button" onClick={() => setAll((x) => !x)} aria-pressed={all}
          className="st-toggle sticky left-0 m-1 inline-flex min-h-10 items-center rounded-lg px-2 text-xs font-medium text-accent hover:bg-surface-2">
          {all ? "Ver menos columnas" : "Ver todas las columnas"}
        </button>
      </div>
    </Frame>
  );
}

/** Ranking de goleadores (guía §F.7). Por contrato (`presentation.py`), `sublabel` es el equipo del jugador. */
function BarsVisual({ v }: { v: Extract<Visual, { type: "bars" }> }) {
  const max = Math.max(1, ...v.rows.map((r) => r.value));
  return (
    <Frame title={v.title} subtitle={v.subtitle} partial={v.partial}>
      <ol className="space-y-2.5 p-3">
        {v.rows.map((r, i) => {
          const rank = r.rank ?? i + 1;
          return (
            <li key={i} className="grid grid-cols-[1.75rem_minmax(0,1fr)_auto] items-center gap-x-2.5 gap-y-1 text-sm">
              <span className={`rounded py-0.5 text-center font-display text-[15px] font-extrabold stretch-score tabular-nums ${rank === 1 ? "bg-gold text-gold-text" : "bg-surface-2 text-ink"}`}>{rank}</span>
              <span className="min-w-0 break-words leading-tight">
                {r.label}
                {r.sublabel && (
                  <span className="mt-0.5 flex items-center gap-1.5 text-[13px] text-ink-2"><TeamCrest name={r.sublabel} size={16} /><span className="min-w-0">{r.sublabel}</span></span>
                )}
              </span>
              <span className="font-display text-lg font-extrabold stretch-score tabular-nums">{r.value}</span>
              <div className="col-start-2 col-end-4 h-1.5 rounded bg-surface-2" role="img" aria-label={`${r.label}: ${r.value}`}>
                <div className="h-1.5 rounded bg-chart-1" style={{ width: `${(r.value / max) * 100}%` }} />
              </div>
            </li>
          );
        })}
      </ol>
    </Frame>
  );
}

function ComparisonVisual({ v }: { v: Extract<Visual, { type: "comparison" }> }) {
  return (
    <Frame title={v.title} subtitle={v.subtitle}>
      <div className="space-y-3 p-3">
        <p className="flex gap-4 text-xs"><span className="text-brand-text">■ {v.series[0]}</span><span className="text-accent">■ {v.series[1]}</span></p>
        {v.rows.map((r, i) => {
          const vals = r.values.map((x) => Math.max(0, x ?? 0));
          const max = Math.max(1, ...vals);
          return (
            <div key={i} className="text-sm">
              <p className="text-xs text-ink-2">{r.label}</p>
              {vals.map((x, j) => (
                <div key={j} className="flex items-center gap-2">
                  <div className="h-2.5 flex-1 rounded bg-surface-2"><div className={`h-2.5 rounded ${j === 0 ? "bg-chart-1" : "bg-chart-2"}`} style={{ width: `${(x / max) * 100}%` }} /></div>
                  <span className="w-8 text-right tabular-nums">{fmt(r.values[j])}</span>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </Frame>
  );
}

const RESULT_STYLE = { V: "bg-success-soft text-success", E: "bg-warning-soft text-warning", D: "bg-danger-soft text-danger" } as const;

function FormVisual({ v }: { v: Extract<Visual, { type: "form" }> }) {
  const w = 240, h = 60, max = Math.max(1, ...v.rows.map((r) => r.points));
  const pts = v.rows.map((r, i) => `${(i / Math.max(1, v.rows.length - 1)) * (w - 10) + 5},${h - 6 - (r.points / max) * (h - 12)}`).join(" ");
  return (
    <Frame title={v.title} subtitle={v.subtitle}>
      <ul className="divide-y divide-line text-sm">
        {v.rows.map((r, i) => (
          <li key={i} className="flex items-center gap-2 px-3 py-1.5">
            <span className="w-8 text-xs text-ink-2">J{r.round ?? "?"}</span>
            <span className="team-name flex-1" title={r.rival}>{r.home ? "vs" : "@"} {r.rival}</span>
            <span className="tabular-nums">{r.score}</span>
            <span className={`w-6 rounded text-center text-xs font-bold ${RESULT_STYLE[r.result]}`} aria-label={{ V: "Victoria", E: "Empate", D: "Derrota" }[r.result]}>{r.result}</span>
          </li>
        ))}
      </ul>
      <svg viewBox={`0 0 ${w} ${h}`} className="h-20 w-full border-t border-line p-2" preserveAspectRatio="xMidYMid meet" role="img" aria-label={`Puntos acumulados: ${v.rows.map((r) => r.points).join(", ")}`}>
        <polyline points={pts} fill="none" className="stroke-chart-1" strokeWidth="2.5" strokeLinejoin="round" />
        {v.rows.map((r, i) => <circle key={i} cx={(i / Math.max(1, v.rows.length - 1)) * (w - 10) + 5} cy={h - 6 - (r.points / max) * (h - 12)} r="3" className="fill-chart-1" />)}
      </svg>
    </Frame>
  );
}

/** Renderiza visuales ESTRUCTURADOS que llegan del backend (nunca se interpreta texto libre; todo se pinta como texto, sin HTML). */
export default function Visuals({ visuals }: { visuals: Visual[] }) {
  return (
    <>
      {visuals.map((v, i) => {
        switch (v.type) {
          case "table": return <TableVisual key={i} v={v} />;
          case "bars": return <BarsVisual key={i} v={v} />;
          case "comparison": return <ComparisonVisual key={i} v={v} />;
          case "form": return <FormVisual key={i} v={v} />;
          case "lineup": return <Lineup key={i} v={v} />;
          case "cards": return <Cards key={i} v={v} />;
          case "officials": return <Officials key={i} v={v} />;
          case "timeline": return <Timeline key={i} v={v} />;
          case "matches": return <Matches key={i} v={v} />;
          default: return null; // tipo desconocido: se ignora, la respuesta de texto sigue siendo la alternativa
        }
      })}
    </>
  );
}
