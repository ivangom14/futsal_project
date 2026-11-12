import { useState, type KeyboardEvent } from "react";

export const MAX_CHARS = 2000;

export default function Composer({ onSend, loading, usesContext = false }: { onSend: (q: string) => void; loading: boolean; usesContext?: boolean }) {
  const [text, setText] = useState("");
  const submit = () => {
    const q = text.trim();
    if (!q || loading) return;
    onSend(q);
    setText("");
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };
  return (
    <div className="border-t border-line bg-surface p-3">
      {usesContext && (
        <p className="mx-auto mb-1 max-w-3xl text-xs text-ink-2" data-testid="context-hint">
          Futsito tiene en cuenta los mensajes anteriores de esta conversación.
        </p>
      )}
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          maxLength={MAX_CHARS}
          rows={2}
          placeholder="Escribe tu pregunta… (Enter envía, Shift+Enter nueva línea)"
          aria-label="Pregunta"
          className="min-w-0 flex-1 resize-none rounded-lg border border-line-strong bg-surface px-3 text-ink py-2 text-base sm:text-sm focus:border-focus focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        />
        <button onClick={submit} disabled={loading || !text.trim()} className="min-h-10 shrink-0 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-on hover:bg-brand-hover disabled:opacity-50">
          Enviar
        </button>
      </div>
    </div>
  );
}
