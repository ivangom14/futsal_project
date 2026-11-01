/** Contrato de `POST /chat` y `GET /chat/health` (servicio `futsal.agent.web`). Ver docs/phase22-frontend.md. */

export interface Source {
  /** `official_data` (datos de la RFFM) | `document` (reglamento, circulares: RAG). */
  kind: "official_data" | "document" | string;
  title: string;
  detail: string | null;
  pages: string | null;
  text: string | null;
  /** Documentos: ¿la respuesta se apoya en este fragmento? `null` = no aplica. */
  cited: boolean | null;
  queried_at: string | null;
  /** Cuándo se actualizaron los datos; `null` = no se conoce (nunca se inventa). */
  updated_at: string | null;
  freshness: "fresh" | "stale" | "known" | "unknown" | null;
  updated_note: string | null;
  meta: Record<string, unknown>;
}

export interface Column { key: string; label: string; align: "left" | "right" }
export type Visual =
  | { type: "table"; title: string; subtitle?: string | null; partial?: boolean; columns: Column[]; rows: Record<string, string | number | boolean | null>[] }
  | { type: "bars"; title: string; subtitle?: string | null; partial?: boolean; rows: { label: string; sublabel?: string | null; value: number; rank?: number | null }[] }
  | { type: "comparison"; title: string; subtitle?: string | null; series: string[]; rows: { label: string; values: (number | null)[] }[] }
  | { type: "form"; title: string; subtitle?: string | null; rows: { round: number | null; rival: string; score: string; result: "V" | "E" | "D"; points: number; home: boolean }[] };

export type Focus = Partial<Record<"team" | "player" | "competition" | "group" | "round", string>>;
export type FeedbackReason = "wrong_data" | "lost_context" | "incomplete" | "bad_source" | "misunderstood" | "other";
export interface Feedback { rating: "up" | "down"; reason?: FeedbackReason; comment?: string }

export interface ToolInfo {
  name: string;
  arguments: Record<string, unknown>;
  duration_ms: number;
  is_error: boolean;
  refused: boolean;
  summary: string;
}

export interface Diagnostics {
  run_id: string;
  provider: string | null;
  model: string | null;
  duration_ms: number;
  resolved_question?: string | null;
  resolved_scope?: { filters: string; inherited: boolean; resolved: boolean; competitions: string[]; total: number } | null;
  history_turns: number;
  llm_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  token_source: string;
  tools: ToolInfo[];
  errors: string[];
}

export interface ChatResponse {
  answer: string;
  guardrail: string | null;
  error: string | null;
  /** De dónde sale lo que se muestra: datos oficiales, documentos o ambos. */
  answer_kind: "official" | "documental" | "mixed" | "none";
  /** Análisis o recomendación del modelo: no es información oficial. */
  is_analysis: boolean;
  sources: Source[];
  visuals: Visual[];
  suggestions: string[];
  focus: Focus;
  diagnostics: Diagnostics;
}

export interface HistoryItem {
  role: "user" | "assistant";
  content: string;
}

export interface Health {
  status: "ok" | "degraded";
  agent: { ready: boolean; error: string | null };
  llm: { provider: string; key_configured: boolean };
  data_api: { reachable: boolean; detail: string | null };
}

/** Estado de conexión mostrado en la interfaz. */
export type Connection =
  | { state: "checking" }
  | { state: "offline"; detail: string }
  | { state: "ok" | "degraded"; health: Health };

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  /** Respuesta del agente (fuentes, diagnóstico, error parcial). */
  response?: ChatResponse;
  /** Mensaje de error del backend: no es una respuesta del agente. */
  feedback?: Feedback;
  failure?: string;
  /** Detalle técnico del fallo (solo para el panel de diagnóstico). */
  failureTechnical?: string;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: Message[];
}
