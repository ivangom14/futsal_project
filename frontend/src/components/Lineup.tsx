import { useState } from "react";
import { Hand, LayoutGrid, List } from "lucide-react";
import Panel from "../ui/Panel";
import type { LineupPlayer, LineupVisual } from "../types";

/** Colocación ORIENTATIVA de los 4 jugadores de campo (el acta no indica posiciones): portero a la izquierda y el resto repartido en la pista. */
const GK = { x: 62, y: 170 };
const SLOTS = [{ x: 190, y: 170 }, { x: 300, y: 62 }, { x: 300, y: 278 }, { x: 468, y: 170 }];

function Marker({ p, x, y }: { p: LineupPlayer; x: number; y: number }) {
  return (
    <g>
      <circle cx={x} cy={y} r="23" fill={p.goalkeeper ? "#3b2416" : "#c0281e"} stroke="#fff" strokeWidth="3" />
      <text x={x} y={y + 6} textAnchor="middle" fontSize="17" fontWeight="800" fill="#fff">{p.shirt ?? "–"}</text>
      {p.captain && (
        <g>
          <circle cx={x + 18} cy={y - 18} r="9" fill="#f5b301" stroke="#3b2416" strokeWidth="1.5" />
          <text x={x + 18} y={y - 14} textAnchor="middle" fontSize="11" fontWeight="800" fill="#3b2416">C</text>
        </g>
      )}
      <text x={x} y={y + 42} textAnchor="middle" fontSize="17" fontWeight="700" fill="#2b1a10" stroke="#fff" strokeWidth="5" paintOrder="stroke">{p.short}</text>
      {p.goalkeeper && <text x={x} y={y + 60} textAnchor="middle" fontSize="13" fill="#6f4a2d" stroke="#fff" strokeWidth="3" paintOrder="stroke">Portero</text>}
    </g>
  );
}

function Court({ v }: { v: LineupVisual }) {
  const keeper = v.starters.find((p) => p.goalkeeper)!;
  const field = v.starters.filter((p) => !p.goalkeeper);
  const label = `Quinteto inicial de ${v.team}: ${v.starters.map((p) => `${p.shirt ?? "sin dorsal"} ${p.name}`).join(", ")}`;
  return (
    <svg viewBox="0 0 600 340" className="w-full" role="img" aria-label={label}>
      <rect x="8" y="8" width="584" height="324" rx="22" fill="#fff" stroke="#2b1a10" strokeWidth="6" />
      <g fill="none" stroke="#2b1a10" strokeWidth="4">
        <line x1="300" y1="8" x2="300" y2="332" />
        <circle cx="300" cy="170" r="56" />
        <path d="M8 80 A90 90 0 0 1 8 260" /><path d="M592 80 A90 90 0 0 0 592 260" />
      </g>
      <circle cx="300" cy="170" r="5" fill="#2b1a10" /><circle cx="104" cy="170" r="5" fill="#2b1a10" /><circle cx="496" cy="170" r="5" fill="#2b1a10" />
      <Marker p={keeper} x={GK.x} y={GK.y} />
      {field.map((p, i) => <Marker key={`${p.shirt}-${p.name}`} p={p} x={SLOTS[i].x} y={SLOTS[i].y} />)}
    </svg>
  );
}

function PlayerRow({ p }: { p: LineupPlayer }) {
  return (
    <li className="flex min-h-9 items-center gap-2.5 py-1 text-[15px]">
      <span className={`grid h-6 w-7 shrink-0 place-items-center rounded-sm font-display text-xs font-bold tabular-nums ${p.goalkeeper ? "bg-inverse text-inverse-on" : "bg-surface-2 text-ink"}`}
        aria-label={p.shirt === null ? "Sin dorsal" : `Dorsal ${p.shirt}`}>{p.shirt ?? "–"}</span>
      <span className="min-w-0 flex-1 leading-tight [overflow-wrap:anywhere]">{p.name}</span>
      {p.goalkeeper && <span className="flex shrink-0 items-center gap-1 text-xs text-ink-2"><Hand size={13} strokeWidth={1.75} aria-hidden="true" />Portero</span>}
      {p.captain && <span className="shrink-0 rounded-sm bg-gold px-1 text-xs font-bold text-gold-text" title="Capitán" aria-label="Capitán">C</span>}
    </li>
  );
}

function Group({ label, players, empty }: { label: string; players: LineupPlayer[]; empty?: string }) {
  return (
    <section aria-label={label}>
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-ink-2">{label}</h3>
      {players.length > 0 ? <ul className="divide-y divide-line">{players.map((p, i) => <PlayerRow key={i} p={p} />)}</ul>
        : <p className="py-1 text-sm text-ink-2">{empty}</p>}
    </section>
  );
}

/**
 * Alineación del acta. Por defecto, LISTA (titulares y suplentes con dorsal, portero y capitán): responde a la pregunta y es accesible.
 * La pista es opcional («Ver en pista») porque el acta no indica posiciones y la colocación sería orientativa.
 */
export default function Lineup({ v }: { v: LineupVisual }) {
  const [court, setCourt] = useState(false);
  const toggle = v.court ? (
    <button type="button" onClick={() => setCourt((c) => !c)} aria-pressed={court}
      className="-my-1 -mr-1 inline-flex min-h-9 items-center gap-1.5 rounded-md px-2 text-xs font-semibold text-accent hover:bg-surface-2">
      {court ? <List size={14} aria-hidden="true" /> : <LayoutGrid size={14} aria-hidden="true" />}{court ? "Ver lista" : "Ver en pista"}
    </button>
  ) : null;
  return (
    <Panel title={v.title} subtitle={v.subtitle} aside={toggle}>
      <div className="grid gap-x-6 gap-y-3 p-3 sm:grid-cols-2">
        {court && v.court ? (
          <div className="sm:col-span-2"><Court v={v} /></div>
        ) : (
          <div>
            <Group label="Titulares" players={v.starters} empty="El acta no indica titulares." />
            {!v.court && v.starters.length > 0 && <p className="mt-1 text-xs text-warning">El acta no permite un quinteto completo (no hay 5 titulares con un portero).</p>}
          </div>
        )}
        <div className={court ? "sm:col-span-2" : ""}>
          <Group label="Suplentes" players={v.substitutes} empty="No constan suplentes en el acta." />
          {v.unknown.length > 0 && <div className="mt-3"><Group label="Sin titularidad indicada" players={v.unknown} /></div>}
        </div>
      </div>
      <p className="border-t border-line px-3 py-2 text-xs text-ink-2">{v.note}</p>
    </Panel>
  );
}
