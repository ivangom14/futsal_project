import { kickoffLabel } from "../format";
import type { MatchesVisual } from "../types";

/**
 * Partidos listados: resultados o próximos encuentros, con su estado, fecha y hora (Europe/Madrid).
 * Los nombres de equipo se muestran COMPLETOS (saltan de línea; nunca se cortan con puntos suspensivos).
 * Maquetación: en tarjetas estrechas (móvil) los equipos van apilados y el VS/marcador queda a la derecha, centrado en vertical;
 * en tarjetas anchas, tres columnas con el VS/marcador en el centro. El cambio depende del ancho de la tarjeta (consulta de contenedor), no de la pantalla.
 */
export default function Matches({ v }: { v: MatchesVisual }) {
  return (
    <figure className="mt-3 overflow-hidden rounded-xl border border-bear-100 bg-white" data-testid="visual">
      <figcaption className="border-b border-bear-100 bg-bear-50 px-3 py-2">
        <p className="text-sm font-semibold text-bear-900">{v.title}</p>
        {v.subtitle && <p className="text-xs text-slate-500">{v.subtitle}</p>}
      </figcaption>
      <ul className="match-list divide-y divide-bear-100">
        {v.rows.map((m, i) => (
          <li key={i} className="text-sm" data-testid="match-row">
            <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-xs text-slate-500">
              <span data-testid="match-when">{m.round ? `Jornada ${m.round} · ` : ""}{kickoffLabel(m.date)}</span>
              {m.status === "finished" ? <span className="rounded bg-slate-100 px-1.5 font-semibold text-slate-600">Finalizado</span>
                : m.status === "scheduled" ? <span className="rounded bg-brand-50 px-1.5 font-semibold text-brand-700">Programado</span>
                : <span className="rounded bg-amber-100 px-1.5 font-semibold text-amber-800">{m.status}</span>}
            </div>
            <div className="match-row mt-1">
              <span className="match-team match-home" title={m.home}>{m.home}</span>
              <span className={`match-mid rounded-md text-center font-bold tabular-nums ${m.score ? "bg-bear-900 text-white" : "bg-bear-100 text-slate-500"}`}>{m.score ?? "vs"}</span>
              <span className="match-team match-away" title={m.away}>{m.away}</span>
            </div>
            {m.past_due && <p className="mt-0.5 text-xs text-amber-700">La fecha ya pasó y sigue como programado: el dato puede estar desactualizado.</p>}
          </li>
        ))}
      </ul>
      {v.partial && <p className="border-t border-bear-100 px-3 py-1.5 text-xs text-slate-500">Se muestran {v.rows.length} de {v.total} partidos.</p>}
    </figure>
  );
}
