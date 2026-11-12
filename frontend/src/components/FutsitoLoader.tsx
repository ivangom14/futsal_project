/**
 * Futsito dando toques al balón mientras el agente trabaja (docs/design-system/05-loader-futsito.md).
 * SVG en línea + CSS (solo `transform`/`opacity`, sin dependencias); con movimiento reducido queda en una pose fija.
 * Decorativo: el texto de estado lo anuncia quien lo usa.
 */
export default function FutsitoLoader({ className = "h-14 w-14" }: { className?: string }) {
  return (
    <svg viewBox="0 0 120 120" className={`futsito-loader shrink-0 ${className}`} aria-hidden="true" focusable="false">
      <ellipse className="fl-shadow" cx="82" cy="114" rx="10" ry="2.5" fill="currentColor" opacity=".18" />
      <circle cx="44" cy="24" r="9" fill="#8b5a36" /><circle cx="84" cy="24" r="9" fill="#8b5a36" />
      <circle cx="44" cy="24" r="4" fill="#d9a77c" /><circle cx="84" cy="24" r="4" fill="#d9a77c" />
      <rect x="52" y="88" width="10" height="20" rx="5" fill="#8b5a36" />
      <g className="fl-leg"><rect x="66" y="88" width="10" height="20" rx="5" fill="#8b5a36" /><ellipse cx="73" cy="107" rx="7" ry="4" fill="#2b1a10" /></g>
      <path d="M42 64q22-8 44 0l4 26q-26 6-52 0z" fill="#c0281e" />
      <circle cx="40" cy="74" r="6" fill="#a8764f" /><circle cx="88" cy="74" r="6" fill="#a8764f" />
      <circle cx="64" cy="42" r="22" fill="#a8764f" />
      <ellipse cx="66" cy="50" rx="11" ry="8" fill="#e8c8a4" /><ellipse cx="66" cy="46" rx="4" ry="3" fill="#2b1a10" />
      <circle cx="58" cy="40" r="2.8" fill="#2b1a10" /><circle cx="76" cy="40" r="2.8" fill="#2b1a10" />
      <g className="fl-ball"><circle cx="82" cy="99" r="8" fill="#fff" stroke="#2b1a10" strokeWidth="2" /><path d="M82 95l3.8 2.8-1.5 4.4h-4.6l-1.5-4.4z" fill="#2b1a10" /></g>
    </svg>
  );
}
