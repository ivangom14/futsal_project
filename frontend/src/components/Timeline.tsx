import type { TimelineVisual } from "../types";

/** Goles del acta por minuto, a la izquierda los del local y a la derecha los del visitante. No dibuja tarjetas (su significado no está verificado). */
export default function Timeline({ v }: { v: TimelineVisual }) {
  return (
    <figure className="mt-3 overflow-hidden rounded-xl border border-bear-100 bg-white" data-testid="visual">
      <figcaption className="border-b border-bear-100 bg-bear-50 px-3 py-2">
        <p className="text-sm font-semibold text-bear-900">{v.title}</p>
        {v.subtitle && <p className="text-xs text-slate-500">{v.subtitle}</p>}
      </figcaption>
      <div className="grid grid-cols-[1fr_auto_1fr] gap-x-2 px-3 pt-2 text-xs font-semibold text-slate-600">
        <span className="truncate text-right">{v.home}</span><span aria-hidden>⚽</span><span className="truncate">{v.away}</span>
      </div>
      <ol className="grid grid-cols-[1fr_auto_1fr] gap-x-2 gap-y-1 px-3 py-2 text-sm">
        {v.events.map((e, i) => (
          <li key={i} className="contents">
            <span className="text-right">{e.side === "home" ? <Who e={e} /> : null}</span>
            <span className="min-w-[2.5rem] rounded-full bg-brand-50 px-2 text-center text-xs font-bold leading-6 text-brand-700" aria-label={e.minute === null ? "minuto desconocido" : `minuto ${e.minute}`}>
              {e.minute === null ? "–" : `${e.minute}'`}
            </span>
            <span>{e.side === "away" ? <Who e={e} /> : null}</span>
          </li>
        ))}
      </ol>
      {v.partial && <p className="px-3 pb-2 text-xs text-amber-700">Se muestran los primeros goles del acta.</p>}
    </figure>
  );
}

function Who({ e }: { e: TimelineVisual["events"][number] }) {
  return <span>{e.player}{e.own_goal && <span className="text-xs text-slate-500"> (en propia puerta)</span>}</span>;
}
