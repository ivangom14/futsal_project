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
  | LineupVisual
  | CardsVisual
  | OfficialsVisual
  | TimelineVisual
  | MatchesVisual
  | { type: "form"; title: string; subtitle?: string | null; rows: { round: number | null; rival: string; score: string; result: "V" | "E" | "D"; points: number; home: boolean }[] };

export interface LineupPlayer { name: string; short: string; shirt: number | null; captain: boolean; goalkeeper: boolean }
export interface LineupVisual {
  type: "lineup"; title: string; subtitle?: string | null; team: string;
  /** Hay exactamente 5 titulares con 1 portero: solo entonces se dibuja la pista. */
  court: boolean; starters: LineupPlayer[]; substitutes: LineupPlayer[]; unknown: LineupPlayer[]; note: string;
}

export interface OfficialsVisual {
  type: "officials"; title: string; subtitle?: string | null;
  /** `kind` solo elige el dibujo: árbitro, asistente o mesa. El cargo es el que figura en el acta. */
  people: { name: string; role: string; kind: "referee" | "assistant" | "table" }[];
}
export interface TimelineVisual {
  type: "timeline"; title: string; subtitle?: string | null; home: string; away: string; partial?: boolean;
  events: { minute: number | null; player: string; side: "home" | "away"; own_goal: boolean }[];
}
export interface MatchesVisual {
  type: "matches"; title: string; subtitle?: string | null; total: number; partial: boolean;
  rows: { round: number | null; date: string | null; home: string; away: string; score: string | null; status: string; past_due: boolean }[];
}

export type CardKind = "yellow" | "second_yellow" | "red" | "unknown";
export interface CardsVisual {
  type: "cards"; title: string; subtitle?: string | null;
  /** Con `home`/`away` (un acta) se dibuja a dos lados; sin ellos (varios partidos) es una lista con el equipo. */
  home: string | null; away: string | null; partial: boolean;
  summary: Record<CardKind, number>;
  events: { minute: number | null; player: string; team: string | null; side: "home" | "away" | null; kind: CardKind; round: number | null }[];
}

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

export interface Reconciliation {
  considered: number;
  removed: number;
  kept: number;
  pointer_added: boolean;
  /** Presente solo si la conciliación falló: se mostró el texto original. */
  error?: string;
  removed_items: { kind: string; reason: string; snippet: string }[];
  kept_uncertain: { kind: string; reason: string; left: string[]; snippet: string }[];
}

export interface Diagnostics {
  run_id: string;
  provider: string | null;
  model: string | null;
  duration_ms: number;
  resolved_question?: string | null;
  resolved_scope?: { filters: string; inherited: boolean; resolved: boolean; competitions: string[]; total: number } | null;
  history_turns: number;
  /** Dónde se fue el tiempo (ms): modelo (con esperas por reintentos), herramientas, contexto y montaje de la respuesta. */
  llm_ms?: number;
  retry_wait_ms?: number;
  llm_retries?: number;
  tools_ms?: number;
  context_ms?: number;
  postprocess_ms?: number;
  /** Conciliación del texto con los visuales: qué se omitió por repetirlos y por qué (fragmentos cortos, nunca la respuesta completa). `null` = no hubo visuales o texto que evaluar. */
  context?: ContextInfo | null;
  reconciliation?: Reconciliation | null;
  input_tokens_peak?: number;
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
  /** Texto original del agente, solo si la conciliación omitió algo de `answer` (está en un gráfico): se reenvía como historial, no se muestra. */
  answer_original?: string | null;
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
  queue?: { busy: boolean; running_for_s: number | null; waiting: number };
  /** AGENT_CONTEXT con el que arrancó el backend (legacy | deterministic | llm). */
  context_mode?: string;
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

/** Comprensión de contexto (solo con AGENT_CONTEXT=deterministic|llm). */
export interface ContextInfo {
  mode?: string;
  state?: string;
  status?: string;
  reason?: string;
  intent?: string;
  intent_confidence?: string;
  competitions?: string[];
  team?: string;
  options?: string[];
  assumption?: string;
  provenance?: Record<string, string>;
  recovery?: string | number;
  interpreter_trigger?: string;
  interpreter_error?: string;
  interpreter?: { intent?: string | null; competitions?: string[]; team?: string | null; confidence?: string; discarded?: string[] };
  error?: string;
  assumption_prepended?: boolean;
}
