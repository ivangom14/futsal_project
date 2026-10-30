/** Contrato de `POST /chat` y `GET /chat/health` (servicio `futsal.agent.web`). Ver docs/phase22-frontend.md. */

export interface Source {
  kind: "rag" | "official_table" | string;
  title: string;
  detail: string | null;
  pages: string | null;
  text: string | null;
  meta: Record<string, unknown>;
}

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
  sources: Source[];
  diagnostics: Diagnostics;
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
  failure?: string;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: Message[];
}
