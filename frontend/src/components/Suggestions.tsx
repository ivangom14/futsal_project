import { CornerDownRight } from "lucide-react";
import Chip from "../ui/Chip";

/** Siguientes preguntas (las genera el backend sin LLM, solo con capacidades reales): chips compactos, sin rótulo que ocupe una línea. */
export default function Suggestions({ items, onPick, disabled }: { items: string[]; onPick: (q: string) => void; disabled: boolean }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Preguntas sugeridas">
      {items.map((q) => (
        <Chip key={q} variant="suggest" disabled={disabled} onClick={() => onPick(q)}>
          <CornerDownRight size={14} aria-hidden="true" className="shrink-0 text-ink-2" />{q}
        </Chip>
      ))}
    </div>
  );
}
