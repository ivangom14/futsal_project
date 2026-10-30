import type { Conversation, Message } from "./types";

export const STORAGE_KEY = "rffm-chat-v1";
export const TITLE_MAX = 48;

export interface State {
  conversations: Conversation[];
  activeId: string | null;
}

export const uid = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

export function titleFrom(text: string): string {
  const t = text.trim().replace(/\s+/g, " ");
  return t.length > TITLE_MAX ? `${t.slice(0, TITLE_MAX - 1)}…` : t;
}

export function createConversation(now = Date.now()): Conversation {
  return { id: uid(), title: "Nueva conversación", createdAt: now, updatedAt: now, messages: [] };
}

const isMessage = (m: unknown): m is Message =>
  typeof m === "object" && m !== null && typeof (m as Message).id === "string" && ((m as Message).role === "user" || (m as Message).role === "assistant") && typeof (m as Message).content === "string";

const isConversation = (c: unknown): c is Conversation =>
  typeof c === "object" && c !== null && typeof (c as Conversation).id === "string" && typeof (c as Conversation).title === "string" &&
  Array.isArray((c as Conversation).messages) && (c as Conversation).messages.every(isMessage);

export function loadState(): State {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const data = JSON.parse(raw) as State;
      if (Array.isArray(data.conversations)) {
        const conversations = data.conversations.filter(isConversation); // descarta entradas con otra forma (versiones antiguas, datos corruptos)
        const active = conversations.some((c) => c.id === data.activeId) ? data.activeId : (conversations[0]?.id ?? null);
        return { conversations, activeId: active };
      }
    }
  } catch {
    /* almacenamiento no disponible o corrupto: se empieza de cero */
  }
  return { conversations: [], activeId: null };
}

export function saveState(state: State): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* cuota o modo privado: la sesión sigue funcionando sin persistencia */
  }
}

export function startConversation(state: State): State {
  const empty = state.conversations.find((c) => c.messages.length === 0);
  if (empty) return { ...state, activeId: empty.id }; // no se acumulan conversaciones vacías
  const conv = createConversation();
  return { conversations: [conv, ...state.conversations], activeId: conv.id };
}

export function selectConversation(state: State, id: string): State {
  return state.conversations.some((c) => c.id === id) ? { ...state, activeId: id } : state;
}

export function deleteConversation(state: State, id: string): State {
  const conversations = state.conversations.filter((c) => c.id !== id);
  const activeId = state.activeId === id ? (conversations[0]?.id ?? null) : state.activeId;
  return { conversations, activeId };
}

export function appendMessage(state: State, convId: string, message: Message): State {
  return {
    ...state,
    conversations: state.conversations.map((c) => {
      if (c.id !== convId) return c;
      const first = c.messages.length === 0 && message.role === "user";
      return { ...c, title: first ? titleFrom(message.content) : c.title, updatedAt: message.createdAt, messages: [...c.messages, message] };
    }),
  };
}

export function removeMessage(state: State, convId: string, messageId: string): State {
  return {
    ...state,
    conversations: state.conversations.map((c) => (c.id === convId ? { ...c, messages: c.messages.filter((m) => m.id !== messageId) } : c)),
  };
}
