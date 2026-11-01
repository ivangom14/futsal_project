export default function Suggestions({ items, onPick, disabled }: { items: string[]; onPick: (q: string) => void; disabled: boolean }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-3" role="group" aria-label="Preguntas sugeridas">
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Podrías preguntar</p>
      <div className="flex flex-wrap gap-2">
        {items.map((q) => (
          <button key={q} disabled={disabled} onClick={() => onPick(q)}
            className="rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-left text-sm text-brand-700 hover:bg-brand-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 disabled:opacity-50">
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
