import { Database, FileText } from "lucide-react";
import type { Source } from "../types";

const DAY = new Intl.DateTimeFormat("es-ES", { timeZone: "Europe/Madrid", day: "2-digit", month: "2-digit", year: "numeric" });
const updated = (iso: string | null) => {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : DAY.format(d);
};

/** Fuentes iguales (mismo título y detalle) se agrupan y sus páginas se juntan. Los fragmentos de documentos que la respuesta NO usa (`cited: false`) no se listan. */
export function groupSources(sources: Source[]): (Source & { pageList: string[] })[] {
  const out = new Map<string, Source & { pageList: string[] }>();
  for (const s of sources) {
    if (s.kind === "document" && s.cited === false) continue;
    const key = `${s.kind}|${s.title}|${s.detail ?? ""}`;
    const prev = out.get(key);
    if (prev) {
      if (s.pages && !prev.pageList.includes(s.pages)) prev.pageList.push(s.pages);
    } else out.set(key, { ...s, pageList: s.pages ? [s.pages] : [] });
  }
  return [...out.values()];
}

/**
 * Pie de fuentes discreto y plegado (guía §F.11): una línea con la primera fuente y, al desplegar, cada una con su detalle,
 * páginas y fecha de actualización (o que no se conoce: nunca se inventa). Sin fuentes, no se pinta nada.
 */
export default function SourceFooter({ sources }: { sources: Source[] }) {
  const items = groupSources(sources);
  if (items.length === 0) return null;
  const first = items[0];
  const Icon = first.kind === "document" ? FileText : Database;
  return (
    <details className="group mt-3 text-[13px] leading-5 text-ink-2" data-testid="sources">
      <summary className="flex min-h-8 cursor-pointer list-none items-center gap-1.5 rounded [&::-webkit-details-marker]:hidden">
        <Icon size={14} strokeWidth={1.75} aria-hidden="true" className="shrink-0" />
        <span className="min-w-0 truncate">
          Fuente: {first.title}{items.length > 1 ? ` y ${items.length - 1} más` : ""}
        </span>
        <span aria-hidden="true" className="shrink-0 transition-transform group-open:rotate-90">›</span>
      </summary>
      <ul className="mt-1 space-y-1 border-l-2 border-line pl-3">
        {items.map((s, i) => {
          const when = updated(s.updated_at);
          return (
            <li key={i}>
              <span className="font-medium text-ink">{s.title}</span>
              {s.detail && <> · {s.detail}</>}
              {s.pageList.length > 0 && <> · p. {s.pageList.join(", ")}</>}
              {s.kind === "official_data" && (
                <span className={s.freshness === "stale" ? "text-warning" : undefined}>
                  {" · "}{when ? `actualizado ${when}` : "fecha de actualización no disponible"}{s.freshness === "stale" ? " (puede estar desactualizado)" : ""}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}
