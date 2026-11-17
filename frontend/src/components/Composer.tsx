import { useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { ArrowUp, Square, X } from "lucide-react";
import type { Focus } from "../types";

export const MAX_CHARS = 2000;
const MAX_HEIGHT = 144; // ~6 líneas: a partir de ahí el campo desplaza por dentro

interface Props {
  onSend: (q: string) => void;
  /** Hay una consulta en curso (en cualquier conversación): no se puede enviar otra. */
  loading: boolean;
  /** Para la consulta en curso de ESTA conversación: el botón de enviar pasa a «Detener». */
  onStop?: () => void;
  /** Foco de la conversación (equipo, competición…) que Futsito arrastra a la siguiente pregunta; solo si ya hay contexto. */
  focus?: Focus;
  /** Quitar el contexto = empezar una conversación nueva (el foco vive en el historial de esta, en el servidor). */
  onClearContext?: () => void;
}

/** «Lope de Vega FS», «PREFERENTE · Grupo 2»… o null si no hay foco que mostrar. */
export function focusLabel(f: Focus | undefined): string | null {
  if (!f) return null;
  const who = f.team ?? f.player;
  if (who) return who;
  return f.competition ? [f.competition, f.group].filter(Boolean).join(" · ") : null;
}

/**
 * Compositor: una sola fila que crece con el texto (hasta ~6 líneas), al alcance del pulgar y pegado al teclado (src/viewport.ts).
 * Enter envía y Shift+Enter salta de línea. Durante la consulta, «Detener» ocupa el sitio de «Enviar». El contexto solo se muestra
 * cuando existe y se puede quitar con un toque.
 */
export default function Composer({ onSend, onStop, loading, focus, onClearContext }: Props) {
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
  const label = focusLabel(focus);
  const near = text.length >= MAX_CHARS - 200;
  return (
    <div className="bg-canvas px-3 pt-1.5 pb-[max(0.5rem,env(safe-area-inset-bottom))] pl-[max(0.75rem,env(safe-area-inset-left))] pr-[max(0.75rem,env(safe-area-inset-right))]">
      <div className="mx-auto max-w-3xl">
        {label && (
          <div className="mb-1.5 flex" data-testid="context-hint">
            <span className="inline-flex min-w-0 max-w-full items-center rounded-full border border-line bg-surface pl-3 text-[13px] text-ink-2"
              title="Futsito tiene en cuenta esta conversación al interpretar tu siguiente pregunta">
              <span className="min-w-0 truncate">Sobre <strong className="font-semibold text-ink">{label}</strong></span>
              {onClearContext && (
                <button type="button" onClick={onClearContext} aria-label={`Quitar el contexto (${label}): empezar una conversación nueva`}
                  title="Empezar una conversación sin este contexto"
                  className="-my-px ml-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full text-ink-2 hover:bg-surface-2 hover:text-ink [@media(pointer:coarse)]:h-9 [@media(pointer:coarse)]:w-9">
                  <X size={15} aria-hidden="true" />
                </button>
              )}
            </span>
          </div>
        )}
        <div className="flex items-end gap-1.5 rounded-[22px] border border-line-strong bg-surface py-1 pl-4 pr-1 transition-shadow focus-within:border-focus focus-within:ring-1 focus-within:ring-focus">
          <textarea
            ref={ref}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKey}
            maxLength={MAX_CHARS}
            rows={1}
            enterKeyHint="send"
            placeholder="Pregunta por partidos, resultados, clasificaciones…"
            aria-label="Pregunta"
            aria-describedby="composer-help"
            className="min-w-0 flex-1 resize-none bg-transparent py-[9px] text-base leading-6 text-ink placeholder:truncate focus:outline-none focus-visible:outline-none"
          />
          {near && <span className="self-center text-xs tabular-nums text-ink-2" data-testid="char-count">{text.length}/{MAX_CHARS}</span>}
          {stop ? (
            <button onClick={onStop} aria-label="Detener" title="Detener la consulta"
              className="grid h-10 w-10 shrink-0 place-items-center rounded-full [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11 bg-inverse text-inverse-on hover:opacity-90">
              <Square size={13} fill="currentColor" aria-hidden="true" />
            </button>
          ) : (
            <button onClick={submit} disabled={loading || !text.trim()} aria-label="Enviar" title="Enviar"
              className="grid h-10 w-10 shrink-0 place-items-center rounded-full [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11 bg-brand text-brand-on transition-colors hover:bg-brand-hover disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-ink-2">
              <ArrowUp size={20} strokeWidth={2.25} aria-hidden="true" />
            </button>
          )}
        </div>
        <p id="composer-help" className="sr-only">Enter envía; Mayúsculas más Enter, nueva línea.</p>
      </div>
    </div>
  );
}
