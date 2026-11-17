import Panel from "../ui/Panel";
import type { OfficialsVisual } from "../types";

/** Árbitros del acta con su cargo tal cual figura. Lista compacta: el cargo como antetítulo y el nombre completo (sin ilustraciones). */
export default function Officials({ v }: { v: OfficialsVisual }) {
  return (
    <Panel title={v.title} subtitle={v.subtitle}>
      <ul className="grid divide-y divide-line sm:grid-cols-2 sm:divide-y-0">
        {v.people.map((p, i) => (
          <li key={i} className="flex min-h-12 items-center gap-3 px-3 py-2" data-testid="official">
            <span aria-hidden="true" className={`h-8 w-1 shrink-0 rounded-full ${p.kind === "referee" ? "bg-ink" : p.kind === "assistant" ? "bg-gold" : "bg-accent"}`} />
            <span className="min-w-0">
              <span className="block text-[11px] font-semibold uppercase tracking-wide text-ink-2">{p.role}</span>
              <span className="block text-[15px] font-medium leading-tight [overflow-wrap:anywhere]">{p.name}</span>
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
