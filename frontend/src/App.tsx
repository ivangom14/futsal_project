import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, askAgent, forgetConversation, getHealth } from "./api";
import {
  appendMessage, deleteConversation, historyFor, loadState, removeMessage, saveState, selectConversation, startConversation, uid,
  type State,
} from "./conversations";
import Composer from "./components/Composer";
import ConnectionBadge from "./components/ConnectionBadge";
import MessageBubble from "./components/MessageBubble";
import Sidebar from "./components/Sidebar";
import Welcome from "./components/Welcome";
import type { Connection, HistoryItem, Message } from "./types";

const HEALTH_POLL_MS = 15_000;

export default function App() {
  const [state, setState] = useState<State>(() => {
    const s = loadState();
    return s.conversations.length ? s : startConversation(s);
  });
  const [loadingId, setLoadingId] = useState<string | null>(null); // conversación con una pregunta en curso
  const [connection, setConnection] = useState<Connection>({ state: "checking" });
  const [menu, setMenu] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  const active = state.conversations.find((c) => c.id === state.activeId) ?? null;

  useEffect(() => {
    saveState(state);
  }, [state]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [active?.messages.length, loadingId]);

  const refreshHealth = useCallback(async () => {
    try {
      const health = await getHealth();
      setConnection({ state: health.status, health });
    } catch (e) {
      setConnection({ state: "offline", detail: e instanceof ApiError ? e.message : "Sin respuesta del servicio" });
    }
  }, []);
  useEffect(() => {
    void refreshHealth();
    const t = setInterval(() => void refreshHealth(), HEALTH_POLL_MS);
    return () => clearInterval(t);
  }, [refreshHealth]);

  const ask = useCallback(
    async (convId: string, question: string, history: HistoryItem[]) => {
      setLoadingId(convId);
      try {
        const response = await askAgent(question, convId, history);
        const msg: Message = { id: uid(), role: "assistant", content: response.answer, createdAt: Date.now(), response };
        setState((s) => appendMessage(s, convId, msg));
      } catch (e) {
        const failure = e instanceof ApiError ? e.message : "Error inesperado";
        setState((s) => appendMessage(s, convId, { id: uid(), role: "assistant", content: "", createdAt: Date.now(), failure }));
        void refreshHealth();
      } finally {
        setLoadingId(null);
      }
    },
    [refreshHealth],
  );

  const send = (question: string) => {
    if (!active || loadingId) return;
    const history = historyFor(active.messages); // contexto de ESTA conversación, sin la pregunta actual
    setState((s) => appendMessage(s, active.id, { id: uid(), role: "user", content: question, createdAt: Date.now() }));
    void ask(active.id, question, history);
  };

  const retry = (failed: Message) => {
    if (!active || loadingId) return;
    const idx = active.messages.findIndex((m) => m.id === failed.id);
    const question = active.messages
      .slice(0, idx)
      .reverse()
      .find((m) => m.role === "user")?.content;
    if (!question) return;
    const history = historyFor(active.messages.slice(0, idx - 1)); // lo anterior a la pregunta que falló
    setState((s) => removeMessage(s, active.id, failed.id));
    void ask(active.id, question, history);
  };

  const loading = loadingId !== null;
  return (
    <div className="relative flex h-full">
      <Sidebar
        conversations={state.conversations}
        activeId={state.activeId}
        open={menu}
        onNew={() => { setState(startConversation); setMenu(false); }}
        onSelect={(id) => { setState((s) => selectConversation(s, id)); setMenu(false); }}
        onDelete={(id) => { void forgetConversation(id).catch(() => undefined); setState((s) => { const n = deleteConversation(s, id); return n.conversations.length ? n : startConversation(n); }); }}
        onClose={() => setMenu(false)}
      />
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-2">
          <button onClick={() => setMenu(true)} aria-label="Abrir conversaciones" className="rounded p-1 md:hidden">☰</button>
          <h1 className="flex-1 truncate text-base font-semibold">⚽ Asistente RFFM <span className="font-normal text-slate-500">· Fútbol sala</span></h1>
          <ConnectionBadge connection={connection} onRefresh={() => void refreshHealth()} />
        </header>
        <div className="flex-1 overflow-y-auto px-4 py-4" data-testid="messages">
          <div className="mx-auto max-w-3xl space-y-4">
            {active && active.messages.length === 0 && <Welcome onPick={send} disabled={loading} />}
            {active?.messages.map((m, i) => (
              <MessageBubble key={m.id} message={m} onRetry={m.failure && i === active.messages.length - 1 ? () => retry(m) : undefined} />
            ))}
            {loadingId !== null && loadingId === active?.id && (
              <p role="status" className="animate-pulse text-sm text-slate-500">El agente está consultando los datos…</p>
            )}
            <div ref={endRef} />
          </div>
        </div>
        <Composer onSend={send} loading={loading} usesContext={historyFor(active?.messages ?? []).length > 0} />
      </main>
    </div>
  );
}
