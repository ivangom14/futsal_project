import { memo, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import { CircleSlash, Lightbulb, RotateCcw, TriangleAlert } from "lucide-react";
import remarkGfm from "remark-gfm";
import FeedbackBar from "./FeedbackBar";
import Suggestions from "./Suggestions";
import TechnicalDetails from "./TechnicalDetails";
import Visuals from "./Visuals";
import SourceFooter from "./SourceFooter";
import { showDiagnostics } from "../diagnostics";
import type { Feedback, Message, Visual } from "../types";
import Button from "../ui/Button";
import Notice from "../ui/Notice";
import Chip from "../ui/Chip";

interface Props {
  message: Message;
  /** Solo en el último mensaje fallido. */
  onRetry?: (failed: Message) => void;
  /** Solo la última respuesta ofrece preguntas sugeridas y «ver más». */
  onSuggest?: (q: string) => void;
  suggestDisabled?: boolean;
  onFeedback: (messageId: string, runId: string | null, fb: Feedback) => Promise<void> | void;
}

/** Sugerencia de paginación que genera el backend (`presentation._more_chip`): se resuelve SIN LLM con la página siguiente. */
export const MORE_RE = /^Muéstrame los siguientes (\d+)$/;
/** Respuestas más largas se pliegan (el texto completo sigue en el DOM para lectores de pantalla y búsqueda). */
export const LONG_ANSWER = 1400;

const isPaged = (v: Visual) => (v.type === "matches" || v.type === "bars") && Boolean(v.partial);

/**
 * Quita del texto SOLO las frases de paginación que la tarjeta ya muestra («Mostrando 1–10 de 48.», «¿Quieres que te muestre los
 * siguientes 10?»). Es presentación determinista: no toca datos ni cifras del resto de la respuesta.
 */
export function tidyAnswer(text: string, paged: boolean): string {
  if (!paged) return text;
  return text
    .replace(/\s*Mostrando \d+\s*[–-]\s*\d+ de \d+\.?/g, "")
    .replace(/\s*¿Quieres que te muestre (?:los|las) siguientes \d+\?/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** Tablas Markdown del agente (comparaciones, listados sin visual): compactas y con desplazamiento propio, nunca de la página. */
const MD: Components = {
  table: ({ children }) => (
    <div className="md-table not-prose my-3 overflow-x-auto rounded-md border border-line bg-surface" tabIndex={0} role="region" aria-label="Tabla (desplazable)">
      <table className="w-full text-sm">{children}</table>
    </div>
  ),
};

function LongText({ long, children }: { long: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  if (!long) return <>{children}</>;
  return (
    <div>
      <div className={open ? "" : "answer-clamp"} data-testid="answer-clamp">{children}</div>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="mt-1 inline-flex min-h-10 items-center rounded-md px-1 text-sm font-semibold text-accent hover:underline">
        {open ? "Mostrar menos" : "Mostrar respuesta completa"}
      </button>
    </div>
  );
}

function MessageBubble({ message, onRetry, onSuggest, suggestDisabled = false, onFeedback }: Props) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end pl-10" data-testid="msg-user">
        <p className="min-w-0 whitespace-pre-wrap break-words rounded-[18px] rounded-br-md bg-bubble px-3.5 py-2 text-base leading-6 text-ink [overflow-wrap:anywhere]">{message.content}</p>
      </div>
    );
  }
  if (message.failure) {
    const cancelled = message.failureCode === "cancelled"; // la ha parado el usuario: no es un error del sistema
    return (
      <div role="alert" data-testid="msg-error"
        className={`rounded-md border px-3.5 py-3 text-sm ${cancelled ? "border-line bg-surface text-ink" : "border-danger bg-danger-soft text-danger"}`}>
        <p className="flex items-center gap-1.5 font-semibold">
          {cancelled ? <CircleSlash size={16} aria-hidden="true" className="shrink-0" /> : <TriangleAlert size={16} aria-hidden="true" className="shrink-0" />}
          {cancelled ? "Consulta cancelada" : "No he podido responder ahora mismo"}
        </p>
        <p className="mt-0.5">{message.failure}</p>
        {message.failureTechnical && (
          <details className="mt-1 text-xs">
            <summary className="cursor-pointer">Detalle técnico</summary>
            <p className="mt-1 break-words font-mono">{message.failureTechnical}</p>
          </details>
        )}
        {onRetry && (
          <Button onClick={() => onRetry(message)} className="mt-2"><RotateCcw size={15} aria-hidden="true" /> Reintentar</Button>
        )}
      </div>
    );
  }
  const r = message.response;
  const diagnostics = showDiagnostics();
  const ctx = r?.diagnostics?.context;
  // Aclaración: el agente pregunta y la comprensión de contexto ya conoce las opciones válidas → se ofrecen pulsables (solo en la última respuesta).
  const options = ctx && (ctx.status === "ask" || ctx.status === "missing") ? (ctx.options ?? []).filter(Boolean) : [];
  const asking = ctx?.status === "ask";
  // Supuesto: el backend lo antepone al texto (assumption_prepended); se separa como nota SOLO si el texto empieza exactamente por él.
  const assumption = ctx?.assumption_prepended && ctx.assumption && message.content.startsWith(ctx.assumption) ? ctx.assumption : null;
  const visuals = r?.visuals ?? [];
  const paged = visuals.some(isPaged);
  const body = tidyAnswer(assumption ? message.content.slice(assumption.length).trimStart() : message.content, paged);
  // «Ver más»: la sugerencia de paginación se convierte en la acción de la propia tarjeta (no en un chip suelto).
  const suggestions = r?.suggestions ?? [];
  const more = paged ? suggestions.find((s) => MORE_RE.test(s)) : undefined;
  const chips = more ? suggestions.filter((s) => s !== more) : suggestions;
  const onMore = more && onSuggest ? () => onSuggest(more) : undefined;
  return (
    <article className={`min-w-0 ${asking ? "border-l-[3px] border-accent pl-3" : ""}`} data-testid="msg-agent" aria-label={asking ? "Aclaración de Futsito" : "Respuesta de Futsito"}>
      {r?.is_analysis && (
        <p className="mb-1.5 flex items-center gap-1.5 text-[13px] font-medium text-warning" data-testid="analysis-label"
          title="Interpretación elaborada por el asistente a partir de los datos">
          <Lightbulb size={15} aria-hidden="true" /> Análisis de Futsito · no es información oficial
        </p>
      )}
      {assumption && <Notice tone="info" className="mb-2" testId="assumption">{assumption}</Notice>}
      {body && (
        <LongText long={body.length > LONG_ANSWER}>
          <div className="answer prose max-w-none break-words text-base leading-relaxed [overflow-wrap:anywhere]">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD}>{body}</ReactMarkdown>
          </div>
        </LongText>
      )}
      {r?.error && (
        <Notice tone="warning" role="status" className="mt-2">
          No he podido completar del todo la consulta.{diagnostics ? " El detalle está en «Detalles técnicos»." : " Puede faltar parte de la información."}
        </Notice>
      )}
      {r && options.length > 0 && (
        // Opciones de la aclaración: botones apilados a todo el ancho en móvil (nombres largos legibles), en fila en pantallas anchas. Solo la última
        // respuesta las deja pulsar; en las anteriores quedan como registro de lo que se ofreció.
        <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:flex-wrap" role="group" aria-label="Opciones" data-testid="clarification-options">
          {options.map((o) => (
            <Chip key={o} variant="option" disabled={!onSuggest || suggestDisabled} onClick={onSuggest ? () => onSuggest(o) : undefined}
              className="w-full justify-start sm:w-auto">{o}</Chip>
          ))}
        </div>
      )}
      {r && <Visuals visuals={visuals} onMore={onMore} moreDisabled={suggestDisabled} />}
      {r && (
        <div className="mt-2 flex flex-wrap items-start gap-x-2">
          <div className="min-w-0 flex-1"><SourceFooter sources={r.sources ?? []} /></div>
          <FeedbackBar value={message.feedback} onSubmit={(fb) => onFeedback(message.id, r.diagnostics.run_id, fb)} />
        </div>
      )}
      {r && onSuggest && options.length === 0 && <Suggestions items={chips} onPick={onSuggest} disabled={suggestDisabled} />}
      {r && diagnostics && <TechnicalDetails response={r} />}
    </article>
  );
}

/** Memorizado: una respuesta ya pintada no vuelve a analizar su Markdown cuando cambia otra parte de la app. */
export default memo(MessageBubble);
