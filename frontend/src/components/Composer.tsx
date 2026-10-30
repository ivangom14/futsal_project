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
    <div className="border-t border-slate-200 bg-white p-3">
      {usesContext && (
        <p className="mx-auto mb-1 max-w-3xl text-xs text-slate-400" data-testid="context-hint">
          El agente tiene en cuenta los mensajes anteriores de esta conversación.
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
          className="flex-1 resize-none rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-pitch-500 focus:outline-none"
        />
        <button onClick={submit} disabled={loading || !text.trim()} className="rounded-lg bg-pitch-600 px-4 py-2 text-sm font-medium text-white hover:bg-pitch-700 disabled:opacity-50">
          Enviar
        </button>
      </div>
    </div>
  );
}
