import type { ChatResponse, Feedback, Focus, Health, HistoryItem } from "./types";

export const API_URL: string = (import.meta.env.VITE_CHAT_API_URL as string | undefined) ?? "http://127.0.0.1:8001";

/** Error de la API (HTTP != 2xx) o de red (`status` 0). */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null = null,
    /** Detalle técnico (ya sin secretos) para el panel de diagnóstico; nunca se muestra como texto principal. */
    readonly technical: string | null = null,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 120_000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, signal: ctrl.signal });
  } catch (e) {
    const aborted = e instanceof DOMException && e.name === "AbortError";
    throw new ApiError(
      aborted ? "La consulta está tardando más de lo esperado. Puedes volver a intentarlo." : "No he podido conectar con el servicio. Inténtalo de nuevo dentro de unos instantes.",
      0,
      aborted ? "timeout" : "connection",
      e instanceof Error ? `${e.name}: ${e.message}` : null,
    );
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let detail = `Error HTTP ${res.status}`;
    let code: string | null = null;
    let technical: string | null = null;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
      if (typeof body?.code === "string") code = body.code;
      if (typeof body?.technical === "string") technical = body.technical;
    } catch {
      /* cuerpo no JSON */
    }
    throw new ApiError(detail, res.status, code, technical);
  }
  return (await res.json()) as T;
}

export const getHealth = (): Promise<Health> => request<Health>("/chat/health", undefined, 5_000);

/** `history` solo siembra la conversación si el servidor no la conoce (p. ej. tras reiniciarlo); el servidor manda. */
export const askAgent = (question: string, conversationId: string, history: HistoryItem[] = [], focus: Focus = {}): Promise<ChatResponse> =>
  request<ChatResponse>("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, conversation_id: conversationId, history, focus }),
  });

/** Valoración de una respuesta (solo para análisis posterior: no cambia el comportamiento del agente). */
export const sendFeedback = (conversationId: string, messageId: string, runId: string | null, fb: Feedback): Promise<{ saved: boolean }> =>
  request("/chat/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: conversationId, message_id: messageId, run_id: runId, rating: fb.rating, reason: fb.reason ?? null, comment: fb.comment ?? null }),
  }, 10_000);

/** Pide al servidor que olvide el historial de una conversación borrada (si falla, caduca solo: memoria acotada). */
export const forgetConversation = (conversationId: string): Promise<{ deleted: boolean }> =>
  request(`/chat/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" }, 5_000);
