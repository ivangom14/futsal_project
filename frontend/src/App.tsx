import { useCallback, useEffect, useRef, useState } from "react";
import { Menu } from "lucide-react";
import { ApiError, askAgent, forgetConversation, getHealth, sendFeedback } from "./api";
import {
  appendMessage, deleteConversation, focusFor, historyFor, loadState, removeMessage, saveState, selectConversation, setFeedback, startConversation, uid,
  type State,
} from "./conversations";
import AgentStatus from "./components/AgentStatus";
import Composer from "./components/Composer";
import ConnectionBadge from "./components/ConnectionBadge";
import MessageBubble from "./components/MessageBubble";
import Sidebar from "./components/Sidebar";
import Welcome from "./components/Welcome";
import { LogoMark } from "./components/Logo";
import type { Connection, Feedback, Focus, HistoryItem, Message } from "./types";

const HEALTH_POLL_MS = 15_000;
const prefersReducedMotion = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
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
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const menuWasOpen = useRef(false);

  const active = state.conversations.find((c) => c.id === state.activeId) ?? null;

  useEffect(() => {
    saveState(state);
  }, [state]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }, [active?.messages.length, loadingId]);
  useEffect(() => {
    // Cajón móvil abierto: el chat queda inerte (ni foco ni lector de pantalla); al cerrarlo, el foco vuelve al botón del menú.
    mainRef.current?.toggleAttribute("inert", menu);
    if (menuWasOpen.current && !menu) menuButtonRef.current?.focus();
    menuWasOpen.current = menu;
  }, [menu]);

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
        const failureCode = e instanceof ApiError ? (e.code ?? undefined) : undefined;
        setState((s) => appendMessage(s, convId, { id: uid(), role: "assistant", content: "", createdAt: Date.now(), failure, failureTechnical, failureCode }));
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

  const cancel = () => abortRef.current?.abort();

  const submitFeedback = async (messageId: string, runId: string | null, fb: Feedback) => {
    if (!active) return;
    await sendFeedback(active.id, messageId, runId, fb); // si falla, la valoración no se marca como enviada
    setState((s) => setFeedback(s, active.id, messageId, fb));
  };

  const loading = loadingId !== null;
  const lastAgentId = [...(active?.messages ?? [])].reverse().find((m) => m.role === "assistant" && m.response)?.id;
  return (
    <div className="relative flex h-full bg-canvas">
      <Sidebar
        conversations={state.conversations}
        activeId={state.activeId}
        open={menu}
        onNew={() => { setState(startConversation); setMenu(false); }}
        onSelect={(id) => { setState((s) => selectConversation(s, id)); setMenu(false); }}
        onDelete={(id) => { void forgetConversation(id).catch(() => undefined); setState((s) => { const n = deleteConversation(s, id); return n.conversations.length ? n : startConversation(n); }); }}
        onClose={() => setMenu(false)}
      />
      <main ref={mainRef} className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-line bg-surface px-4 py-2">
          <button ref={menuButtonRef} onClick={() => setMenu(true)} aria-label="Abrir conversaciones" aria-haspopup="dialog" aria-expanded={menu}
            className="-ml-2 grid h-10 w-10 place-items-center rounded-lg text-ink hover:bg-surface-2 md:hidden">
            <Menu size={22} aria-hidden="true" />
          </button>
          <LogoMark className="h-7 w-7 md:hidden" />
          <h1 className="flex-1 truncate font-display text-lg font-extrabold tracking-tight text-brand-text stretch-brand">Futsito <span className="hidden font-sans text-base font-normal tracking-normal text-ink-2 sm:inline" style={{ fontStretch: "100%" }}>· Asistente de fútbol sala RFFM</span></h1>
          <ConnectionBadge connection={connection} onRefresh={() => void refreshHealth()} />
        </header>
        <div className="flex-1 overflow-y-auto overflow-x-hidden px-4 py-4" data-testid="messages">
          <div className="mx-auto max-w-3xl space-y-6">
            {active && active.messages.length === 0 && <Welcome onPick={send} disabled={loading} />}
            {active?.messages.map((m, i) => (
              <MessageBubble key={m.id} message={m} onRetry={m.failure && i === active.messages.length - 1 ? () => retry(m) : undefined}
                onSuggest={m.id === lastAgentId ? send : undefined} suggestDisabled={loading} onFeedback={submitFeedback} />
            ))}
            {loadingId !== null && loadingId === active?.id && <AgentStatus slow={slow} onCancel={cancel} />}
            <div ref={endRef} />
          </div>
        </div>
        <Composer onSend={send} onStop={loadingId !== null && loadingId === active?.id ? cancel : undefined} loading={loading} usesContext={historyFor(active?.messages ?? []).length > 0} />
      </main>
    </div>
  );
}
