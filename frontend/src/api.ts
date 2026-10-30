import type { ChatResponse, Health, HistoryItem } from "./types";

export const API_URL: string = (import.meta.env.VITE_CHAT_API_URL as string | undefined) ?? "http://127.0.0.1:8001";

/** Error de la API (HTTP != 2xx) o de red (`status` 0). */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
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
    throw new ApiError(aborted ? "La petición tardó demasiado" : "No se pudo conectar con el servicio de chat", 0);
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let detail = `Error HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* cuerpo no JSON */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

export const getHealth = (): Promise<Health> => request<Health>("/chat/health", undefined, 5_000);

/** `history` solo siembra la conversación si el servidor no la conoce (p. ej. tras reiniciarlo); el servidor manda. */
export const askAgent = (question: string, conversationId: string, history: HistoryItem[] = []): Promise<ChatResponse> =>
  request<ChatResponse>("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, conversation_id: conversationId, history }),
  });

/** Pide al servidor que olvide el historial de una conversación borrada (si falla, caduca solo: memoria acotada). */
export const forgetConversation = (conversationId: string): Promise<{ deleted: boolean }> =>
  request(`/chat/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" }, 5_000);
