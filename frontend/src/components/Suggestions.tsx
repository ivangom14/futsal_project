export default function Suggestions({ items, onPick, disabled }: { items: string[]; onPick: (q: string) => void; disabled: boolean }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-3" role="group" aria-label="Preguntas sugeridas">
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-2">Podrías preguntar</p>
      <div className="flex flex-wrap gap-2">
        {items.map((q) => (
          <button key={q} disabled={disabled} onClick={() => onPick(q)}
            className="min-h-10 rounded-full border border-line bg-brand-soft px-3 py-2 text-left text-sm text-brand-text hover:border-brand disabled:opacity-50">
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
