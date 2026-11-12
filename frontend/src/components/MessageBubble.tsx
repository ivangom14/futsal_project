import ReactMarkdown from "react-markdown";
import { CircleSlash, TriangleAlert } from "lucide-react";
import remarkGfm from "remark-gfm";
import { LogoMark } from "./Logo";
import FeedbackBar from "./FeedbackBar";
import Suggestions from "./Suggestions";
import TechnicalDetails from "./TechnicalDetails";
import Visuals from "./Visuals";
import SourceFooter from "./SourceFooter";
import { showDiagnostics } from "../diagnostics";
import type { ChatResponse, Feedback, Message } from "../types";

interface Props {
  message: Message;
  onRetry?: () => void;
  /** Solo la última respuesta ofrece preguntas sugeridas. */
  onSuggest?: (q: string) => void;
  suggestDisabled?: boolean;
  onFeedback: (messageId: string, runId: string | null, fb: Feedback) => Promise<void> | void;
}

const KIND_LABEL: Record<ChatResponse["answer_kind"], string | null> = {
  official: "Datos oficiales RFFM", documental: "Normativa y documentos", mixed: "Datos oficiales y normativa", none: null,
};

function Badges({ r }: { r: ChatResponse }) {
  const label = KIND_LABEL[r.answer_kind];
  if (!label && !r.is_analysis) return null;
  return (
    <span className="flex flex-wrap gap-1.5 text-xs">
      {label && <span className="rounded-full bg-brand-soft px-2 py-0.5 font-medium text-brand-text">{label}</span>}
      {r.is_analysis && (
        <span className="rounded-full bg-warning-soft px-2 py-0.5 font-medium text-warning" title="Interpretación elaborada por el asistente a partir de los datos">
          Análisis o recomendación · no es información oficial
        </span>
      )}
    </span>
  );
}

export default function MessageBubble({ message, onRetry, onSuggest, suggestDisabled = false, onFeedback }: Props) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end" data-testid="msg-user">
        <p className="min-w-0 max-w-[85%] whitespace-pre-wrap break-words [overflow-wrap:anywhere] rounded-2xl rounded-br-md bg-inverse px-4 py-2 text-inverse-on">{message.content}</p>
      </div>
    );
  }
  if (message.failure) {
    const cancelled = message.failureCode === "cancelled"; // la ha parado el usuario: no es un error del sistema
    return (
      <div role="alert" data-testid="msg-error"
        className={`rounded-xl border px-4 py-3 text-sm ${cancelled ? "border-line bg-surface-2 text-ink" : "border-danger bg-danger-soft text-danger"}`}>
        <p className="flex items-center gap-1.5 font-semibold">
          {cancelled ? <CircleSlash size={16} aria-hidden="true" className="shrink-0" /> : <TriangleAlert size={16} aria-hidden="true" className="shrink-0" />}
          {cancelled ? "Consulta cancelada" : "No he podido responder ahora mismo"}
        </p>
        <p>{message.failure}</p>
        {message.failureTechnical && (
          <details className="mt-1 text-xs text-danger">
            <summary className="cursor-pointer">Detalle técnico</summary>
            <p className="mt-1 break-words font-mono">{message.failureTechnical}</p>
          </details>
        )}
        {onRetry && (
          <button onClick={onRetry} className="mt-2 min-h-10 rounded-lg border border-line-strong bg-surface px-3 py-1.5 font-medium text-ink hover:bg-surface-2">
            Reintentar
          </button>
        )}
      </div>
    );
  }
  const r = message.response;
  const diagnostics = showDiagnostics();
  // Turno del agente SIN burbuja y a todo el ancho: las tarjetas deportivas ganan ~70 px en móvil (guía §F.1).
  return (
    <article className="min-w-0" data-testid="msg-agent" aria-label="Respuesta de Futsito">
      <header className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
        <LogoMark className="h-7 w-7 shrink-0" />
        <span className="font-display text-sm font-bold text-ink">Futsito</span>
        {r && <Badges r={r} />}
      </header>
      <div className="prose max-w-none break-words text-[15px] leading-relaxed [overflow-wrap:anywhere] sm:text-base prose-p:my-2 prose-table:text-sm">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
      </div>
      {r?.error && (
        <p role="status" className="mt-2 rounded-lg bg-warning-soft px-3 py-2 text-sm text-warning">
          No he podido completar del todo la consulta.{diagnostics ? " El detalle está en «Detalles técnicos»." : " Puede faltar parte de la información."}
        </p>
      )}
      {r && <Visuals visuals={r.visuals ?? []} />}
      {r && <SourceFooter sources={r.sources ?? []} />}
      {r && <FeedbackBar value={message.feedback} onSubmit={(fb) => onFeedback(message.id, r.diagnostics.run_id, fb)} />}
      {r && onSuggest && <Suggestions items={r.suggestions ?? []} onPick={onSuggest} disabled={suggestDisabled} />}
      {r && diagnostics && <TechnicalDetails response={r} />}
    </article>
  );
}
