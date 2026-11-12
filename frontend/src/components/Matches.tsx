import { kickoffLabel, matchStatus, type MatchTone } from "../format";
import type { MatchesVisual } from "../types";

/**
 * Partidos listados: resultados o próximos encuentros, con su estado, fecha y hora (Europe/Madrid).
 * Los nombres de equipo se muestran COMPLETOS (saltan de línea; nunca se cortan con puntos suspensivos).
 * Maquetación: en tarjetas estrechas (móvil) los equipos van apilados y el VS/marcador queda a la derecha, centrado en vertical;
 * en tarjetas anchas, tres columnas con el VS/marcador en el centro. El cambio depende del ancho de la tarjeta (consulta de contenedor), no de la pantalla.
 */
export default function Matches({ v }: { v: MatchesVisual }) {
  return (
    <figure className="mt-3 overflow-hidden rounded-xl border border-line bg-surface" data-testid="visual">
      <figcaption className="border-b border-line bg-surface-2 px-3 py-2">
        <p className="font-display text-sm font-bold text-ink">{v.title}</p>
        {v.subtitle && <p className="text-xs text-ink-2">{v.subtitle}</p>}
      </figcaption>
      <ul className="match-list divide-y divide-line">
        {v.rows.map((m, i) => (
          <li key={i} className="text-sm" data-testid="match-row">
            <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-xs text-ink-2">
              <span data-testid="match-when">{m.round ? `Jornada ${m.round} · ` : ""}{kickoffLabel(m.date)}</span>
              <StatusChip status={m.status} />
            </div>
            <div className="match-row mt-1">
              <span className="match-team match-home" title={m.home}>{m.home}</span>
              <span className={`match-mid rounded-md text-center font-display font-extrabold stretch-score tabular-nums ${m.score ? "bg-inverse text-inverse-on" : "bg-surface-2 text-ink-2"}`}>{m.score ?? "vs"}</span>
              <span className="match-team match-away" title={m.away}>{m.away}</span>
            </div>
            {m.past_due && <p className="mt-0.5 text-xs text-warning">La fecha ya pasó y sigue como programado: el dato puede estar desactualizado.</p>}
          </li>
        ))}
      </ul>
      {v.partial && <p className="border-t border-line px-3 py-1.5 text-xs text-ink-2">Se muestran {v.rows.length} de {v.total} partidos.</p>}
    </figure>
  );
}

const TONE: Record<MatchTone, string> = {
  neutral: "bg-surface-2 text-ink-2",
  info: "bg-accent-soft text-accent",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
};

/** Estado del partido siempre en español (nunca `postponed`, `unknown`…); el valor original queda en `title` si no se reconoce. */
function StatusChip({ status }: { status: string }) {
  const s = matchStatus(status);
  return <span className={`rounded px-1.5 font-semibold ${TONE[s.tone]}`} data-testid="match-status" title={s.label === "Sin confirmar" ? `Estado en la fuente: ${status || "vacío"}` : undefined}>{s.label}</span>;
}
