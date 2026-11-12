import { useState } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import type { Feedback, FeedbackReason } from "../types";

export const REASONS: { value: FeedbackReason; label: string }[] = [
  { value: "wrong_data", label: "Dato incorrecto" },
  { value: "lost_context", label: "Ha perdido el contexto" },
  { value: "incomplete", label: "Respuesta incompleta" },
  { value: "bad_source", label: "Fuente incorrecta o no respaldada" },
  { value: "misunderstood", label: "Ha interpretado mal la pregunta" },
  { value: "other", label: "Otro" },
];

/** Pulgar arriba/abajo bajo cada respuesta. Con 👎 se pide el motivo y un comentario opcional. Solo recoge información para mejorar el agente. */
export default function FeedbackBar({ value, onSubmit }: { value?: Feedback; onSubmit: (f: Feedback) => Promise<void> | void }) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState<FeedbackReason | "">("");
  const [comment, setComment] = useState("");
  const [error, setError] = useState(false);
  const [changing, setChanging] = useState(false);
  const send = async (fb: Feedback) => {
    setError(false);
    try {
      await onSubmit(fb);
      setAsking(false);
      setChanging(false);
    } catch {
      setError(true); // no se pierde la valoración escrita: se puede reintentar
    }
  };
  if (value && !asking && !changing) {
    return (
      <p className="mt-2 flex items-center gap-2 text-xs text-ink-2" role="status">
        {value.rating === "up" ? <ThumbsUp size={14} aria-hidden="true" className="shrink-0 text-success" /> : <ThumbsDown size={14} aria-hidden="true" className="shrink-0" />}
        {value.rating === "up" ? "Gracias por tu valoración" : `Gracias, lo tendremos en cuenta${value.reason ? ` (${REASONS.find((r) => r.value === value.reason)?.label})` : ""}`}
        <button className="inline-flex min-h-6 items-center rounded underline" onClick={() => setChanging(true)}>Cambiar</button>
      </p>
    );
  }
  return (
    <div className="mt-2 text-xs text-ink-2">
      {error && !asking && <p role="alert" className="mb-1 text-danger">No se ha podido guardar la valoración. Inténtalo de nuevo.</p>}
      {!asking && (
        <div className="flex items-center gap-1" role="group" aria-label="Valorar respuesta">
          <button aria-label="Respuesta útil" title="Respuesta útil" className="grid h-10 w-10 place-items-center rounded-lg text-ink-2 hover:bg-surface-2 hover:text-ink" onClick={() => void send({ rating: "up" })}><ThumbsUp size={18} strokeWidth={1.75} aria-hidden="true" /></button>
          <button aria-label="Respuesta mejorable" title="Respuesta mejorable" className="grid h-10 w-10 place-items-center rounded-lg text-ink-2 hover:bg-surface-2 hover:text-ink" onClick={() => setAsking(true)}><ThumbsDown size={18} strokeWidth={1.75} aria-hidden="true" /></button>
        </div>
      )}
      {asking && (
        <form className="mt-1 space-y-2 rounded-lg border border-line bg-surface-2 p-2" onSubmit={(e) => { e.preventDefault(); if (reason) void send({ rating: "down", reason, comment: comment.trim() || undefined }); }}>
          <fieldset>
            <legend className="mb-1 font-medium text-ink">¿Qué ha fallado?</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {REASONS.map((r) => (
                <label key={r.value} className="flex min-h-6 items-center gap-1">
                  <input type="radio" name="reason" value={r.value} checked={reason === r.value} onChange={() => setReason(r.value)} /> {r.label}
                </label>
              ))}
            </div>
          </fieldset>
          <label className="block">
            <span className="sr-only">Comentario opcional</span>
            <textarea value={comment} onChange={(e) => setComment(e.target.value)} maxLength={1000} rows={2} placeholder="Comentario (opcional)" className="w-full rounded border border-line-strong bg-surface px-2 py-1 text-base text-ink sm:text-sm" />
          </label>
          {error && <p role="alert" className="text-danger">No se ha podido guardar la valoración. Inténtalo de nuevo.</p>}
          <div className="flex gap-2">
            <button type="submit" disabled={!reason} className="min-h-10 rounded-lg bg-brand px-3 py-1 text-brand-on hover:bg-brand-hover disabled:opacity-50">Enviar valoración</button>
            <button type="button" className="min-h-10 rounded-lg px-2 underline" onClick={() => setAsking(false)}>Cancelar</button>
          </div>
        </form>
      )}
    </div>
  );
}
