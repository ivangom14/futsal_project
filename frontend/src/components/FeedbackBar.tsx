import { useState } from "react";
import type { Feedback, FeedbackReason } from "../types";

export const REASONS: { value: FeedbackReason; label: string }[] = [
  { value: "wrong_data", label: "Dato incorrecto" },
  { value: "lost_context", label: "Ha perdido el contexto" },
  { value: "incomplete", label: "Respuesta incompleta" },
  { value: "bad_source", label: "Fuente incorrecta o no respaldada" },
  { value: "misunderstood", label: "Ha interpretado mal la pregunta" },
  { value: "other", label: "Otro" },
];

/** 👍/👎 bajo cada respuesta. Con 👎 se pide el motivo y un comentario opcional. Solo recoge información para mejorar el agente. */
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
      <p className="mt-2 flex items-center gap-2 text-xs text-slate-500" role="status">
        {value.rating === "up" ? "👍 Gracias por tu valoración" : `👎 Gracias, lo tendremos en cuenta${value.reason ? ` (${REASONS.find((r) => r.value === value.reason)?.label})` : ""}`}
        <button className="underline" onClick={() => setChanging(true)}>Cambiar</button>
      </p>
    );
  }
  return (
    <div className="mt-2 text-xs text-slate-500">
      {error && !asking && <p role="alert" className="mb-1 text-red-700">No se ha podido guardar la valoración. Inténtalo de nuevo.</p>}
      {!asking && (
        <div className="flex items-center gap-1" role="group" aria-label="Valorar respuesta">
          <button aria-label="Respuesta útil" title="Respuesta útil" className="rounded px-1.5 py-0.5 text-base hover:bg-bear-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500" onClick={() => void send({ rating: "up" })}>👍</button>
          <button aria-label="Respuesta mejorable" title="Respuesta mejorable" className="rounded px-1.5 py-0.5 text-base hover:bg-bear-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500" onClick={() => setAsking(true)}>👎</button>
        </div>
      )}
      {asking && (
        <form className="mt-1 space-y-2 rounded-lg border border-bear-100 bg-bear-50 p-2" onSubmit={(e) => { e.preventDefault(); if (reason) void send({ rating: "down", reason, comment: comment.trim() || undefined }); }}>
          <fieldset>
            <legend className="mb-1 font-medium text-slate-700">¿Qué ha fallado?</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {REASONS.map((r) => (
                <label key={r.value} className="flex items-center gap-1">
                  <input type="radio" name="reason" value={r.value} checked={reason === r.value} onChange={() => setReason(r.value)} /> {r.label}
                </label>
              ))}
            </div>
          </fieldset>
          <label className="block">
            <span className="sr-only">Comentario opcional</span>
            <textarea value={comment} onChange={(e) => setComment(e.target.value)} maxLength={1000} rows={2} placeholder="Comentario (opcional)" className="w-full rounded border border-slate-300 px-2 py-1" />
          </label>
          {error && <p role="alert" className="text-red-700">No se ha podido guardar la valoración. Inténtalo de nuevo.</p>}
          <div className="flex gap-2">
            <button type="submit" disabled={!reason} className="rounded bg-brand-600 px-3 py-1 text-white hover:bg-brand-700 disabled:opacity-50">Enviar valoración</button>
            <button type="button" className="underline" onClick={() => setAsking(false)}>Cancelar</button>
          </div>
        </form>
      )}
    </div>
  );
}
