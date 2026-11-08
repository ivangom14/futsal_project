import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { LogoMark } from "./Logo";
import FeedbackBar from "./FeedbackBar";
import Suggestions from "./Suggestions";
import TechnicalDetails from "./TechnicalDetails";
import Visuals from "./Visuals";
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
    <p className="mb-2 flex flex-wrap gap-2 text-xs">
      {label && <span className="rounded-full bg-brand-50 px-2 py-0.5 font-medium text-brand-700">{label}</span>}
      {r.is_analysis && (
        <span className="rounded-full bg-amber-100 px-2 py-0.5 font-medium text-amber-800" title="Interpretación elaborada por el asistente a partir de los datos">
          Análisis o recomendación · no es información oficial
        </span>
      )}
    </p>
  );
}

export default function MessageBubble({ message, onRetry, onSuggest, suggestDisabled = false, onFeedback }: Props) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end" data-testid="msg-user">
        <p className="min-w-0 max-w-[85%] whitespace-pre-wrap break-words [overflow-wrap:anywhere] rounded-2xl rounded-br-sm bg-brand-600 px-4 py-2 text-white shadow-sm">{message.content}</p>
      </div>
    );
  }
  if (message.failure) {
    return (
      <div role="alert" className="max-w-[85%] rounded-2xl rounded-bl-sm border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-800" data-testid="msg-error">
        <p className="font-semibold">No he podido responder ahora mismo</p>
        <p>{message.failure}</p>
        {message.failureTechnical && (
          <details className="mt-1 text-xs text-red-700">
            <summary className="cursor-pointer">Detalle técnico</summary>
            <p className="mt-1 break-words font-mono">{message.failureTechnical}</p>
          </details>
        )}
        {onRetry && (
          <button onClick={onRetry} className="mt-2 rounded bg-red-600 px-3 py-1 text-white hover:bg-red-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-800">
            Reintentar
          </button>
        )}
      </div>
    );
  }
  const r = message.response;
  return (
    <div className="flex items-start gap-2" data-testid="msg-agent">
      <LogoMark className="mt-1 h-8 w-8 shrink-0" />
      <div className="min-w-0 max-w-[92%] flex-1 rounded-2xl rounded-tl-sm border border-bear-100 bg-white px-4 py-3 shadow-sm">
        {r && <Badges r={r} />}
        <div className="prose prose-sm max-w-none break-words [overflow-wrap:anywhere] prose-table:text-sm">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
        </div>
        {r?.error && (
          <p role="status" className="mt-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">
            No he podido completar del todo la consulta. El detalle está en «Detalles técnicos».
          </p>
        )}
        {r && <Visuals visuals={r.visuals ?? []} />}
        {r && onSuggest && <Suggestions items={r.suggestions ?? []} onPick={onSuggest} disabled={suggestDisabled} />}
        {r && <FeedbackBar value={message.feedback} onSubmit={(fb) => onFeedback(message.id, r.diagnostics.run_id, fb)} />}
        {r && <TechnicalDetails response={r} />}
      </div>
    </div>
  );
}
