import Chip from "../ui/Chip";
export default function Suggestions({ items, onPick, disabled }: { items: string[]; onPick: (q: string) => void; disabled: boolean }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-3" role="group" aria-label="Preguntas sugeridas">
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-2">Podrías preguntar</p>
      <div className="flex flex-wrap gap-2">
        {items.map((q) => (
          <Chip key={q} variant="suggest" disabled={disabled} onClick={() => onPick(q)}>{q}</Chip>
        ))}
      </div>
    </div>
  );
}
