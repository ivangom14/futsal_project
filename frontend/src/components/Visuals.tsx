import type { Visual } from "../types";

const fmt = (v: unknown) => (v === null || v === undefined ? "–" : String(v));

function Frame({ title, subtitle, partial, children }: { title: string; subtitle?: string | null; partial?: boolean; children: React.ReactNode }) {
  return (
    <figure className="mt-3 overflow-hidden rounded-xl border border-bear-100 bg-white" data-testid="visual">
      <figcaption className="border-b border-bear-100 bg-bear-50 px-3 py-2">
        <p className="text-sm font-semibold text-bear-900">{title}</p>
        {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
        {partial && <p className="text-xs text-amber-700">Datos incompletos en la fuente: puede faltar información.</p>}
      </figcaption>
      {children}
    </figure>
  );
}

function TableVisual({ v }: { v: Extract<Visual, { type: "table" }> }) {
  return (
    <Frame title={v.title} subtitle={v.subtitle} partial={v.partial}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-slate-500">
              {v.columns.map((c) => (
                <th key={c.key} scope="col" className={`px-2 py-1 font-medium ${c.align === "right" ? "text-right" : "text-left"}`}>{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {v.rows.map((r, i) => (
              <tr key={i} className={`border-t border-bear-100 ${r.highlight ? "bg-brand-50 font-semibold" : ""}`}>
                {v.columns.map((c) => (
                  <td key={c.key} className={`px-2 py-1 ${c.align === "right" ? "text-right tabular-nums" : "text-left"}`}>{fmt(r[c.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Frame>
  );
}

function BarsVisual({ v }: { v: Extract<Visual, { type: "bars" }> }) {
  const max = Math.max(1, ...v.rows.map((r) => r.value));
  return (
    <Frame title={v.title} subtitle={v.subtitle} partial={v.partial}>
      <ol className="space-y-1.5 p-3">
        {v.rows.map((r, i) => (
          <li key={i} className="text-sm">
            <div className="flex justify-between gap-2">
              <span className="truncate"><span className="mr-1 text-slate-400">{r.rank ?? i + 1}.</span>{r.label}{r.sublabel && <span className="text-slate-500"> · {r.sublabel}</span>}</span>
              <span className="font-semibold tabular-nums">{r.value}</span>
            </div>
            <div className="mt-0.5 h-2 rounded bg-bear-100" role="img" aria-label={`${r.label}: ${r.value}`}>
              <div className="h-2 rounded bg-brand-500" style={{ width: `${(r.value / max) * 100}%` }} />
            </div>
          </li>
        ))}
      </ol>
    </Frame>
  );
}

function ComparisonVisual({ v }: { v: Extract<Visual, { type: "comparison" }> }) {
  return (
    <Frame title={v.title} subtitle={v.subtitle}>
      <div className="space-y-3 p-3">
        <p className="flex gap-4 text-xs"><span className="text-brand-600">■ {v.series[0]}</span><span className="text-bear-500">■ {v.series[1]}</span></p>
        {v.rows.map((r, i) => {
          const vals = r.values.map((x) => Math.max(0, x ?? 0));
          const max = Math.max(1, ...vals);
          return (
            <div key={i} className="text-sm">
              <p className="text-xs text-slate-500">{r.label}</p>
              {vals.map((x, j) => (
                <div key={j} className="flex items-center gap-2">
                  <div className="h-2.5 flex-1 rounded bg-bear-100"><div className={`h-2.5 rounded ${j === 0 ? "bg-brand-500" : "bg-bear-500"}`} style={{ width: `${(x / max) * 100}%` }} /></div>
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

const RESULT_STYLE = { V: "bg-green-100 text-green-800", E: "bg-amber-100 text-amber-800", D: "bg-red-100 text-red-800" } as const;

function FormVisual({ v }: { v: Extract<Visual, { type: "form" }> }) {
  const w = 240, h = 60, max = Math.max(1, ...v.rows.map((r) => r.points));
  const pts = v.rows.map((r, i) => `${(i / Math.max(1, v.rows.length - 1)) * (w - 10) + 5},${h - 6 - (r.points / max) * (h - 12)}`).join(" ");
  return (
    <Frame title={v.title} subtitle={v.subtitle}>
      <ul className="divide-y divide-bear-100 text-sm">
        {v.rows.map((r, i) => (
          <li key={i} className="flex items-center gap-2 px-3 py-1.5">
            <span className="w-8 text-xs text-slate-400">J{r.round ?? "?"}</span>
            <span className="flex-1 truncate">{r.home ? "vs" : "@"} {r.rival}</span>
            <span className="tabular-nums">{r.score}</span>
            <span className={`w-6 rounded text-center text-xs font-bold ${RESULT_STYLE[r.result]}`} aria-label={{ V: "Victoria", E: "Empate", D: "Derrota" }[r.result]}>{r.result}</span>
          </li>
        ))}
      </ul>
      <svg viewBox={`0 0 ${w} ${h}`} className="h-20 w-full border-t border-bear-100 p-2" preserveAspectRatio="xMidYMid meet" role="img" aria-label={`Puntos acumulados: ${v.rows.map((r) => r.points).join(", ")}`}>
        <polyline points={pts} fill="none" stroke="#c0281e" strokeWidth="2.5" strokeLinejoin="round" />
        {v.rows.map((r, i) => <circle key={i} cx={(i / Math.max(1, v.rows.length - 1)) * (w - 10) + 5} cy={h - 6 - (r.points / max) * (h - 12)} r="3" fill="#c0281e" />)}
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
          default: return null; // tipo desconocido: se ignora, la respuesta de texto sigue siendo la alternativa
        }
      })}
    </>
  );
}
