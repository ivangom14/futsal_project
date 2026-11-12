import type { CardKind, CardsVisual } from "../types";

/** Significado de los códigos RFFM 100/101, confirmado por el responsable del proyecto (el acta no los etiqueta). Un código desconocido no lleva color. */
export const CARD_LABEL: Record<CardKind, string> = {
  yellow: "Amarilla",
  second_yellow: "2.ª amarilla (expulsión)",
  red: "Roja directa",
  unknown: "Código sin interpretar",
};
const SUMMARY_LABEL: Record<CardKind, [string, string]> = {
  yellow: ["amarilla", "amarillas"],
  second_yellow: ["segunda amarilla (expulsión)", "segundas amarillas (expulsión)"],
  red: ["roja directa", "rojas directas"],
  unknown: ["tarjeta sin interpretar", "tarjetas sin interpretar"],
};

function Icon({ kind }: { kind: CardKind }) {
  return (
    <svg viewBox="0 0 26 24" width="26" height="24" role="img" aria-label={CARD_LABEL[kind]} className="shrink-0">
      {kind === "second_yellow" && (
        <>
          <rect x="3" y="3" width="11" height="17" rx="2" fill="#f5c518" stroke="#3b2416" strokeWidth="1.3" />
          <rect x="11" y="5" width="11" height="17" rx="2" fill="#d12a1f" stroke="#3b2416" strokeWidth="1.3" />
        </>
      )}
      {kind === "yellow" && <rect x="7" y="3" width="12" height="18" rx="2" fill="#f5c518" stroke="#3b2416" strokeWidth="1.3" />}
      {kind === "red" && <rect x="7" y="3" width="12" height="18" rx="2" fill="#d12a1f" stroke="#3b2416" strokeWidth="1.3" />}
      {kind === "unknown" && (
        <>
          <rect x="7" y="3" width="12" height="18" rx="2" fill="#e5e0d8" stroke="#8b7a68" strokeWidth="1.3" strokeDasharray="2 2" />
          <text x="13" y="16" textAnchor="middle" fontSize="11" fontWeight="700" fill="#6b5a48">?</text>
        </>
      )}
    </svg>
  );
}

function Who({ e, showTeam }: { e: CardsVisual["events"][number]; showTeam: boolean }) {
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      <Icon kind={e.kind} />
      <span className="min-w-0">
        <span className="block break-words text-sm leading-tight">{e.player}{showTeam && e.team && <span className="text-xs text-ink-2"> · {e.team}</span>}</span>
        <span className="block break-words text-xs leading-tight text-ink-2">{CARD_LABEL[e.kind]}</span>
      </span>
    </span>
  );
}

const minuteBadge = (m: number | null) => (m === null ? "–" : `${m}'`);

/** Tarjetas del acta (o de varios partidos) con su significado verificado; las de código desconocido salen sin color. */
export default function Cards({ v }: { v: CardsVisual }) {
  const sides = v.home !== null || v.away !== null;
  const summary = (Object.keys(SUMMARY_LABEL) as CardKind[]).filter((k) => v.summary[k] > 0);
  return (
    <figure className="mt-3 overflow-hidden rounded-xl border border-line bg-surface" data-testid="visual">
      <figcaption className="border-b border-line bg-surface-2 px-3 py-2">
        <p className="font-display text-sm font-bold text-ink">{v.title}</p>
        {v.subtitle && <p className="text-xs text-ink-2">{v.subtitle}</p>}
      </figcaption>
      <p className="flex flex-wrap gap-x-3 gap-y-1 px-3 pt-2 text-xs font-medium text-ink-2" data-testid="cards-summary">
        {summary.map((k) => <span key={k}>{v.summary[k]} {SUMMARY_LABEL[k][v.summary[k] === 1 ? 0 : 1]}</span>)}
      </p>
      {sides ? (
        <>
          <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] gap-x-2 px-3 pt-2 text-xs font-semibold text-ink-2">
            <span className="team-name justify-self-end text-right" title={v.home ?? undefined}>{v.home}</span><span aria-hidden="true" className="self-center"><Icon kind="second_yellow" /></span><span className="team-name" title={v.away ?? undefined}>{v.away}</span>
          </div>
          <ol className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-x-2 gap-y-1.5 px-3 py-2">
            {v.events.map((e, i) => (
              <li key={i} className="contents">
                <span className="flex justify-end text-right">{e.side === "home" ? <Who e={e} showTeam={false} /> : null}</span>
                <span className="min-w-[2.5rem] self-center rounded-full bg-brand-soft px-2 text-center text-xs font-display font-bold leading-6 text-brand-text tabular-nums" aria-label={e.minute === null ? "minuto desconocido" : `minuto ${e.minute}`}>{minuteBadge(e.minute)}</span>
                <span>{e.side !== "home" ? <Who e={e} showTeam={e.side === null} /> : null}</span>
              </li>
            ))}
          </ol>
        </>
      ) : (
        <ol className="divide-y divide-line px-3 py-1">
          {v.events.map((e, i) => (
            <li key={i} className="flex items-center gap-2 py-1.5">
              <span className="w-14 shrink-0 text-xs text-ink-2">{e.round ? `J${e.round} · ` : ""}{minuteBadge(e.minute)}</span>
              <Who e={e} showTeam />
            </li>
          ))}
        </ol>
      )}
      {v.partial && <p className="border-t border-line px-3 py-1.5 text-xs text-warning">Lista parcial: la herramienta limitó el número de tarjetas.</p>}
    </figure>
  );
}
