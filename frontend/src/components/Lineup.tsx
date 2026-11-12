import { Hand } from "lucide-react";
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
    <li className="flex items-center gap-2 py-1 text-sm">
      <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-display text-xs font-bold tabular-nums ${p.goalkeeper ? "bg-inverse text-inverse-on" : "bg-brand text-brand-on"}`}>{p.shirt ?? "–"}</span>
      <span className="min-w-0 flex-1 truncate">{p.name}</span>
      {p.goalkeeper && <Hand size={14} strokeWidth={1.75} className="shrink-0 text-ink-2" role="img" aria-label="Portero"><title>Portero</title></Hand>}
      {p.captain && <span className="rounded bg-gold px-1 text-xs font-bold text-gold-text" title="Capitán">C</span>}
    </li>
  );
}

/** Quinteto inicial sobre la pista y suplentes a la derecha (apilados en móvil). Solo pinta lo que llega del acta. */
export default function Lineup({ v }: { v: LineupVisual }) {
  return (
    <figure className="mt-3 overflow-hidden rounded-xl border border-line bg-surface" data-testid="visual">
      <figcaption className="border-b border-line bg-surface-2 px-3 py-2">
        <p className="font-display text-sm font-bold text-ink">{v.title}</p>
        {v.subtitle && <p className="text-xs text-ink-2">{v.subtitle}</p>}
      </figcaption>
      <div className="grid gap-3 p-3 md:grid-cols-[minmax(0,1fr)_200px]">
        <div>
          {v.court ? (
            <Court v={v} />
          ) : (
            <section aria-label="Titulares">
              <p className="text-xs font-semibold uppercase tracking-wide text-ink-2">Titulares</p>
              <ul>{v.starters.map((p, i) => <PlayerRow key={i} p={p} />)}</ul>
              <p className="mt-1 text-xs text-warning">El acta no permite dibujar un quinteto completo (no hay 5 titulares con un portero).</p>
            </section>
          )}
        </div>
        <section aria-label="Suplentes">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink-2">Suplentes</p>
          {v.substitutes.length > 0 ? <ul className="divide-y divide-line">{v.substitutes.map((p, i) => <PlayerRow key={i} p={p} />)}</ul>
            : <p className="py-1 text-sm text-ink-2">No constan suplentes en el acta.</p>}
          {v.unknown.length > 0 && (
            <>
              <p className="mt-2 text-xs font-semibold uppercase tracking-wide text-ink-2">Sin titularidad indicada</p>
              <ul className="divide-y divide-line">{v.unknown.map((p, i) => <PlayerRow key={i} p={p} />)}</ul>
            </>
          )}
        </section>
      </div>
      <p className="border-t border-line px-3 py-1.5 text-xs text-ink-2">{v.note}</p>
    </figure>
  );
}
