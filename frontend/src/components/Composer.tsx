import { useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { SendHorizontal, Square } from "lucide-react";

export const MAX_CHARS = 2000;
const MAX_HEIGHT = 168; // ~6 líneas: a partir de ahí el campo desplaza por dentro

interface Props {
  onSend: (q: string) => void;
  /** Hay una consulta en curso (en cualquier conversación): no se puede enviar otra. */
  loading: boolean;
  /** Para la consulta en curso de ESTA conversación: el botón de enviar pasa a «Detener». */
  onStop?: () => void;
  usesContext?: boolean;
}

/** Caja de entrada (guía §F.2): crece con el texto, Enter envía, Shift+Enter salta de línea; durante la consulta, «Detener» al alcance del pulgar. */
export default function Composer({ onSend, onStop, loading, usesContext = false }: Props) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [text]);
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
  const stop = loading && onStop;
  const near = text.length >= MAX_CHARS - 200;
  return (
    <div className="border-t border-line bg-surface px-3 pt-2 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
      {usesContext && (
        <p className="mx-auto mb-1.5 max-w-3xl text-xs text-ink-2" data-testid="context-hint">
          Futsito tiene en cuenta los mensajes anteriores de esta conversación.
        </p>
      )}
      <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-line-strong bg-surface py-1.5 pl-3.5 pr-1.5 focus-within:ring-2 focus-within:ring-focus">
        <textarea
          ref={ref}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          maxLength={MAX_CHARS}
          rows={1}
          placeholder="Pregunta a Futsito…"
          aria-label="Pregunta"
          aria-describedby="composer-help"
          className="min-w-0 flex-1 resize-none bg-transparent py-2 text-base leading-6 text-ink focus:outline-none focus-visible:outline-none"
        />
        {stop ? (
          <button onClick={onStop} aria-label="Detener" title="Detener la consulta"
            className="grid h-10 w-10 shrink-0 place-items-center rounded-full border border-line-strong bg-surface text-ink hover:bg-surface-2">
            <Square size={14} fill="currentColor" aria-hidden="true" />
          </button>
        ) : (
          <button onClick={submit} disabled={loading || !text.trim()} aria-label="Enviar" title="Enviar"
            className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-brand text-brand-on hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-50">
            <SendHorizontal size={20} aria-hidden="true" />
          </button>
        )}
      </div>
      <p id="composer-help" className="mx-auto mt-1 flex max-w-3xl justify-between gap-2 px-1 text-xs text-ink-2">
        <span className="hidden [@media(pointer:fine)]:inline">Enter envía · Shift+Enter, nueva línea</span>
        {near && <span className="ml-auto tabular-nums" data-testid="char-count">{text.length}/{MAX_CHARS}</span>}
      </p>
    </div>
  );
}
