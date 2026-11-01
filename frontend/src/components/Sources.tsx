import type { Source } from "../types";

const fmtDate = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric" });
};

function Freshness({ s }: { s: Source }) {
  if (s.updated_at) {
    return (
      <p className={`text-xs ${s.freshness === "stale" ? "text-amber-700" : "text-slate-500"}`}>
        Datos actualizados el {fmtDate(s.updated_at)}{s.queried_at && <> · consultado el {fmtDate(s.queried_at)}</>}
        {s.updated_note && <> · {s.updated_note}</>}
      </p>
    );
  }
  return <p className="text-xs text-amber-700">{s.updated_note ?? "No se conoce la fecha de actualización de estos datos."}</p>;
}

function Item({ s }: { s: Source }) {
  return (
    <li>
      <details className="rounded-lg border border-bear-100 bg-bear-50 px-2 py-1 text-sm">
        <summary className="cursor-pointer rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
          <span className="font-medium">{s.title}</span>
          {s.pages && <span className="text-slate-500"> · p. {s.pages}</span>}
          {s.detail && <span className="text-slate-500"> · {s.detail}</span>}
        </summary>
        <div className="mt-1 space-y-1 text-slate-700">
          {s.kind === "official_data" && <Freshness s={s} />}
          {s.text && <blockquote className="whitespace-pre-wrap border-l-2 border-brand-500 pl-2 text-xs">{s.text}</blockquote>}
          {s.kind === "document" && Object.keys(s.meta).length > 0 && (
            <p className="text-xs text-slate-500">{Object.entries(s.meta).map(([k, v]) => `${k}: ${String(v)}`).join(" · ")}</p>
          )}
        </div>
      </details>
    </li>
  );
}

export default function Sources({ sources }: { sources: Source[] }) {
  if (sources.length === 0) return null;
  const official = sources.filter((s) => s.kind === "official_data");
  const cited = sources.filter((s) => s.kind === "document" && s.cited);
  const consulted = sources.filter((s) => s.kind === "document" && !s.cited);
  return (
    <section aria-label="Fuentes" className="mt-3 space-y-2 border-t border-bear-100 pt-2">
      {official.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Datos oficiales RFFM</p>
          <ul className="space-y-1">{official.map((s, i) => <Item key={i} s={s} />)}</ul>
        </div>
      )}
      {cited.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Normativa citada</p>
          <ul className="space-y-1">{cited.map((s, i) => <Item key={i} s={s} />)}</ul>
        </div>
      )}
      {consulted.length > 0 && (
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer">Otros fragmentos consultados, no citados en la respuesta ({consulted.length})</summary>
          <ul className="mt-1 space-y-1">{consulted.map((s, i) => <Item key={i} s={s} />)}</ul>
        </details>
      )}
    </section>
  );
}
