import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../types";
import Sources from "./Sources";
import TechnicalDetails from "./TechnicalDetails";

export default function MessageBubble({ message, onRetry }: { message: Message; onRetry?: () => void }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end" data-testid="msg-user">
        <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-pitch-600 px-4 py-2 text-white">{message.content}</p>
      </div>
    );
  }
  if (message.failure) {
    return (
      <div role="alert" className="max-w-[85%] rounded-2xl rounded-bl-sm border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-800" data-testid="msg-error">
        <p className="font-semibold">No se pudo obtener una respuesta del agente</p>
        <p>{message.failure}</p>
        {onRetry && (
          <button onClick={onRetry} className="mt-2 rounded bg-red-600 px-3 py-1 text-white hover:bg-red-700">
            Reintentar
          </button>
        )}
      </div>
    );
  }
  const r = message.response;
  return (
    <div className="max-w-[92%] rounded-2xl rounded-bl-sm border border-slate-200 bg-white px-4 py-3 shadow-sm" data-testid="msg-agent">
      <div className="prose prose-sm max-w-none prose-table:text-sm">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
      </div>
      {r?.error && (
        <p role="status" className="mt-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-800">
          Ejecución incompleta: {r.error}
        </p>
      )}
      {r && <Sources sources={r.sources} />}
      {r && <TechnicalDetails response={r} />}
    </div>
  );
}
