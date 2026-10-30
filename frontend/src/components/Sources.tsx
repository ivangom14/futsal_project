import type { Source } from "../types";

const KIND_LABEL: Record<string, string> = { rag: "Normativa", official_table: "Tabla oficial" };

export default function Sources({ sources }: { sources: Source[] }) {
  if (sources.length === 0) return null;
  return (
    <section aria-label="Fuentes" className="mt-3 border-t border-slate-200 pt-2">
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Fuentes consultadas ({sources.length})
      </p>
      <ul className="space-y-1">
        {sources.map((s, i) => (
          <li key={i}>
            <details className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-sm">
              <summary className="cursor-pointer">
                <span className="mr-2 rounded bg-pitch-100 px-1.5 py-0.5 text-xs text-pitch-700">{KIND_LABEL[s.kind] ?? s.kind}</span>
                <span className="font-medium">{s.title}</span>
                {s.pages && <span className="text-slate-500"> · p. {s.pages}</span>}
              </summary>
              <div className="mt-1 space-y-1 text-slate-700">
                {s.detail && <p className="text-xs text-slate-500">{s.detail}</p>}
                {s.text && <blockquote className="whitespace-pre-wrap border-l-2 border-pitch-500 pl-2 text-xs">{s.text}</blockquote>}
                {Object.keys(s.meta).length > 0 && (
                  <dl className="flex flex-wrap gap-x-3 text-xs text-slate-500">
                    {Object.entries(s.meta).map(([k, v]) => (
                      <div key={k}>
                        <dt className="inline">{k}: </dt>
                        <dd className="inline">{String(v)}</dd>
                      </div>
                    ))}
                  </dl>
                )}
              </div>
            </details>
          </li>
        ))}
      </ul>
    </section>
  );
}
