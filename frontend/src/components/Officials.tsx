import type { OfficialsVisual } from "../types";

const KIT = {
  referee: { shirt: "#1f1f1f", trim: "#f5c518", shorts: "#1f1f1f" },
  assistant: { shirt: "#f5c518", trim: "#1f1f1f", shorts: "#1f1f1f" },
  table: { shirt: "#2b5aa8", trim: "#ffffff", shorts: "#1f1f1f" },
} as const;
const SKIN = "#f1c9a5";

/** Monigote pequeño vestido de árbitro (negro con cuello amarillo y silbato), de asistente (amarillo) o de mesa (azul con cronómetro). */
function Figure({ kind, label }: { kind: keyof typeof KIT; label: string }) {
  const k = KIT[kind];
  return (
    <svg viewBox="0 0 48 78" width="52" height="84" role="img" aria-label={label} className="shrink-0">
      <ellipse cx="24" cy="75" rx="13" ry="2.5" fill="#000" opacity=".12" />
      <g stroke={SKIN} strokeWidth="5" strokeLinecap="round"><line x1="19" y1="59" x2="19" y2="70" /><line x1="29" y1="59" x2="29" y2="70" /></g>
      <g fill="#1f1f1f"><rect x="16.5" y="64" width="5" height="7" rx="1.5" /><rect x="26.5" y="64" width="5" height="7" rx="1.5" />
        <ellipse cx="19" cy="72" rx="4.4" ry="2.2" /><ellipse cx="29" cy="72" rx="4.4" ry="2.2" /></g>
      <rect x="13" y="48" width="22" height="13" rx="3" fill={k.shorts} />
      <g stroke={k.shirt} strokeWidth="6" strokeLinecap="round"><line x1="12" y1="27" x2="5" y2="43" /><line x1="36" y1="27" x2="43" y2="43" /></g>
      <g fill={SKIN}><circle cx="5" cy="45" r="3" /><circle cx="43" cy="45" r="3" /></g>
      <path d="M12 24 h24 l-1.5 26 h-21 z" fill={k.shirt} />
      <path d="M12 38 h24 M12.6 44 h22.8" stroke={k.trim} strokeWidth="1.6" opacity=".9" />
      <path d="M18.5 23.5 L24 31 L29.5 23.5 z" fill={k.trim} />
      <circle cx="24" cy="14" r="9.5" fill={SKIN} stroke="#3b2416" strokeWidth="1.4" />
      <path d="M14.8 12 c1-7 17.4-7 18.4 0 c-4-3-14.4-3-18.4 0 z" fill="#3b2416" />
      <circle cx="20.5" cy="14.5" r="1.2" fill="#2b1a10" /><circle cx="27.5" cy="14.5" r="1.2" fill="#2b1a10" />
      <path d="M20.5 19 q3.5 2.4 7 0" fill="none" stroke="#3b2416" strokeWidth="1.3" strokeLinecap="round" />
      {kind !== "table" ? (
        <g><circle cx="31.5" cy="18.5" r="2.6" fill="#f5c518" stroke="#3b2416" strokeWidth="1" /><line x1="28" y1="18.8" x2="29.3" y2="18.8" stroke="#3b2416" strokeWidth="1.2" /></g>
      ) : (
        <g transform="translate(40 48)"><circle r="4.6" fill="#fff" stroke="#1f1f1f" strokeWidth="1.2" /><path d="M0 0 V-3 M0 0 L2.2 1" stroke="#1f1f1f" strokeWidth="1" /></g>
      )}
    </svg>
  );
}

/** Árbitros del acta con su cargo tal cual figura, cada uno con su monigote. */
export default function Officials({ v }: { v: OfficialsVisual }) {
  return (
    <figure className="mt-3 overflow-hidden rounded-xl border border-bear-100 bg-white" data-testid="visual">
      <figcaption className="border-b border-bear-100 bg-bear-50 px-3 py-2">
        <p className="text-sm font-semibold text-bear-900">{v.title}</p>
        {v.subtitle && <p className="text-xs text-slate-500">{v.subtitle}</p>}
      </figcaption>
      <ul className="grid gap-2 p-3 sm:grid-cols-2 md:grid-cols-3">
        {v.people.map((p, i) => (
          <li key={i} className="flex items-center gap-2 rounded-lg bg-bear-50 px-2 py-1">
            <Figure kind={p.kind} label={`${p.role}: ${p.name}`} />
            <span className="min-w-0">
              <span className="block text-xs font-semibold uppercase tracking-wide text-slate-500">{p.role}</span>
              <span className="block text-sm font-medium">{p.name}</span>
            </span>
          </li>
        ))}
      </ul>
    </figure>
  );
}
