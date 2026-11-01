import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, askAgent, forgetConversation, getHealth, sendFeedback } from "./api";
import {
  appendMessage, deleteConversation, focusFor, historyFor, loadState, removeMessage, saveState, selectConversation, setFeedback, startConversation, uid,
  type State,
} from "./conversations";
import Composer from "./components/Composer";
import ConnectionBadge from "./components/ConnectionBadge";
import MessageBubble from "./components/MessageBubble";
import Sidebar from "./components/Sidebar";
import Welcome from "./components/Welcome";
import { LogoMark } from "./components/Logo";
import type { Connection, Feedback, Focus, HistoryItem, Message } from "./types";

const HEALTH_POLL_MS = 15_000;
export const SLOW_MS = 20_000;

export default function App() {
  const [state, setState] = useState<State>(() => {
    const s = loadState();
    return s.conversations.length ? s : startConversation(s);
  });
  const [loadingId, setLoadingId] = useState<string | null>(null); // conversación con una pregunta en curso
  const [connection, setConnection] = useState<Connection>({ state: "checking" });
  const [menu, setMenu] = useState(false);
  const [slow, setSlow] = useState(false); // la consulta lleva más de SLOW_MS
  const abortRef = useRef<AbortController | null>(null);
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
    async (convId: string, question: string, history: HistoryItem[], focus: Focus) => {
      setLoadingId(convId);
      setSlow(false);
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const timer = setTimeout(() => setSlow(true), SLOW_MS);
      try {
        const response = await askAgent(question, convId, history, focus, ctrl.signal);
        const msg: Message = { id: uid(), role: "assistant", content: response.answer, createdAt: Date.now(), response };
        setState((s) => appendMessage(s, convId, msg));
      } catch (e) {
        const failure = e instanceof ApiError ? e.message : "Ha ocurrido un problema al procesar tu consulta.";
        const failureTechnical = e instanceof ApiError ? (e.technical ?? undefined) : e instanceof Error ? e.message : undefined;
        setState((s) => appendMessage(s, convId, { id: uid(), role: "assistant", content: "", createdAt: Date.now(), failure, failureTechnical }));
        void refreshHealth();
      } finally {
        clearTimeout(timer);
        abortRef.current = null;
        setSlow(false);
        setLoadingId(null);
      }
    },
    [refreshHealth],
  );

  const send = (question: string) => {
    if (!active || loadingId) return;
    const history = historyFor(active.messages); // contexto de ESTA conversación, sin la pregunta actual
    const focus = focusFor(active.messages);
    setState((s) => appendMessage(s, active.id, { id: uid(), role: "user", content: question, createdAt: Date.now() }));
    void ask(active.id, question, history, focus);
  };

  const retry = (failed: Message) => {
    if (!active || loadingId) return;
    const idx = active.messages.findIndex((m) => m.id === failed.id);
    const question = active.messages
      .slice(0, idx)
      .reverse()
      .find((m) => m.role === "user")?.content;
    if (!question) return;
    const before = active.messages.slice(0, idx - 1); // lo anterior a la pregunta que falló
    setState((s) => removeMessage(s, active.id, failed.id));
    void ask(active.id, question, historyFor(before), focusFor(before));
  };

  const submitFeedback = async (messageId: string, runId: string | null, fb: Feedback) => {
    if (!active) return;
    await sendFeedback(active.id, messageId, runId, fb); // si falla, la valoración no se marca como enviada
    setState((s) => setFeedback(s, active.id, messageId, fb));
  };

  const loading = loadingId !== null;
  const lastAgentId = [...(active?.messages ?? [])].reverse().find((m) => m.role === "assistant" && m.response)?.id;
  return (
    <div className="relative flex h-full bg-bear-50">
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
        <header className="flex items-center gap-3 border-b border-bear-100 bg-white px-4 py-2">
          <button onClick={() => setMenu(true)} aria-label="Abrir conversaciones" className="rounded p-1 text-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 md:hidden">☰</button>
          <LogoMark className="h-7 w-7 md:hidden" />
          <h1 className="flex-1 truncate text-base font-extrabold tracking-tight text-brand-700">Futsito <span className="hidden font-normal text-slate-500 sm:inline">· Asistente de fútbol sala RFFM</span></h1>
          <ConnectionBadge connection={connection} onRefresh={() => void refreshHealth()} />
        </header>
        <div className="flex-1 overflow-y-auto px-4 py-4" data-testid="messages">
          <div className="mx-auto max-w-3xl space-y-4">
            {active && active.messages.length === 0 && <Welcome onPick={send} disabled={loading} />}
            {active?.messages.map((m, i) => (
              <MessageBubble key={m.id} message={m} onRetry={m.failure && i === active.messages.length - 1 ? () => retry(m) : undefined}
                onSuggest={m.id === lastAgentId ? send : undefined} suggestDisabled={loading} onFeedback={submitFeedback} />
            ))}
            {loadingId !== null && loadingId === active?.id && (
              <div role="status" className="text-sm text-slate-500">
                <p className="animate-pulse">Futsito está consultando los datos…</p>
                {slow && (
                  <p className="mt-1 text-xs text-amber-700" data-testid="slow-hint">
                    Esto está tardando más de lo normal. Puede que el servicio de IA esté limitando las peticiones; puedes esperar o cancelar.
                  </p>
                )}
                <button onClick={() => abortRef.current?.abort()} className="mt-1 text-xs underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">Cancelar</button>
              </div>
            )}
            <div ref={endRef} />
          </div>
        </div>
        <Composer onSend={send} loading={loading} usesContext={historyFor(active?.messages ?? []).length > 0} />
      </main>
    </div>
  );
}
