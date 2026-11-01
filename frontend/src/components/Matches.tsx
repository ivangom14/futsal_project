import type { MatchesVisual } from "../types";

const fmtDate = (iso: string | null) => {
  if (!iso) return "Sin fecha";
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("es-ES", { weekday: "short", day: "2-digit", month: "2-digit" });
};

/** Partidos listados: resultados o próximos encuentros, con su estado. Un partido programado con fecha pasada se avisa (datos posiblemente desactualizados). */
export default function Matches({ v }: { v: MatchesVisual }) {
  return (
    <figure className="mt-3 overflow-hidden rounded-xl border border-bear-100 bg-white" data-testid="visual">
      <figcaption className="border-b border-bear-100 bg-bear-50 px-3 py-2">
        <p className="text-sm font-semibold text-bear-900">{v.title}</p>
        {v.subtitle && <p className="text-xs text-slate-500">{v.subtitle}</p>}
      </figcaption>
      <ul className="divide-y divide-bear-100">
        {v.rows.map((m, i) => (
          <li key={i} className="px-3 py-2 text-sm">
            <div className="flex items-center justify-between gap-2 text-xs text-slate-500">
              <span>{m.round ? `Jornada ${m.round} · ` : ""}{fmtDate(m.date)}</span>
              {m.status === "finished" ? <span className="rounded bg-slate-100 px-1.5 font-semibold text-slate-600">Finalizado</span>
                : m.status === "scheduled" ? <span className="rounded bg-brand-50 px-1.5 font-semibold text-brand-700">Programado</span>
                : <span className="rounded bg-amber-100 px-1.5 font-semibold text-amber-800">{m.status}</span>}
            </div>
            <div className="mt-0.5 grid grid-cols-[1fr_auto_1fr] items-center gap-2">
              <span className="truncate text-right font-medium">{m.home}</span>
              <span className={`min-w-[3.5rem] rounded-md px-2 py-0.5 text-center font-bold tabular-nums ${m.score ? "bg-bear-900 text-white" : "bg-bear-100 text-slate-500"}`}>{m.score ?? "vs"}</span>
              <span className="truncate font-medium">{m.away}</span>
            </div>
            {m.past_due && <p className="mt-0.5 text-xs text-amber-700">La fecha ya pasó y sigue como programado: el dato puede estar desactualizado.</p>}
          </li>
        ))}
      </ul>
      {v.partial && <p className="border-t border-bear-100 px-3 py-1.5 text-xs text-slate-500">Se muestran {v.rows.length} de {v.total} partidos.</p>}
    </figure>
  );
}
