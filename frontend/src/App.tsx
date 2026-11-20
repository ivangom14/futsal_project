import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowDown, Menu, SquarePen } from "lucide-react";
import { ApiError, askAgent, forgetConversation, getHealth, sendFeedback } from "./api";
import {
  appendMessage, changeSport, deleteConversation, focusFor, restoreConversation, historyFor, loadState, removeMessage, saveState, selectConversation, setFeedback, startConversation, uid,
  type State,
} from "./conversations";
import AgentStatus from "./components/AgentStatus";
import Composer from "./components/Composer";
import ConnectionBadge from "./components/ConnectionBadge";
import MessageBubble from "./components/MessageBubble";
import SportSwitch from "./components/SportSwitch";
import Sidebar from "./components/Sidebar";
import Welcome from "./components/Welcome";
import UndoToast from "./components/UndoToast";
import { LogoMark } from "./components/Logo";
import { CrestProvider } from "./crests";
import { trackVisualViewport } from "./viewport";
import { DEFAULT_SPORT, type Connection, type Conversation, type Feedback, type Focus, type HistoryItem, type Message, type Sport } from "./types";

const HEALTH_POLL_MS = 15_000;
const prefersReducedMotion = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
export const SLOW_MS = 20_000;
/** Distancia al final (px) a partir de la que se ofrece «Ir al final». */
const FAR_FROM_END = 480;

function scrollBox(el: HTMLElement, top: number, smooth: boolean) {
  if (typeof el.scrollTo === "function") el.scrollTo({ top, behavior: smooth && !prefersReducedMotion() ? "smooth" : "auto" });
  else el.scrollTop = top;
}

/** Lleva el inicio del ÚLTIMO turno (la pregunta del usuario) arriba del área de lectura: una respuesta larga se lee desde el principio. */
function scrollToLastTurn(el: HTMLElement, smooth: boolean) {
  const questions = el.querySelectorAll<HTMLElement>('[data-testid="msg-user"]');
  const q = questions[questions.length - 1];
  if (!q) return scrollBox(el, el.scrollHeight, smooth);
  const top = q.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop - 12;
  scrollBox(el, Math.max(0, top), smooth);
}

export default function App() {
  const [state, setState] = useState<State>(() => {
    const s = loadState();
    return s.conversations.length ? s : startConversation(s);
  });
  const [loadingId, setLoadingId] = useState<string | null>(null); // conversación con una pregunta en curso
  const [connection, setConnection] = useState<Connection>({ state: "checking" });
  const [menu, setMenu] = useState(false);
  const [slow, setSlow] = useState(false); // la consulta lleva más de SLOW_MS
  const [farFromEnd, setFarFromEnd] = useState(false);
  const [deleted, setDeleted] = useState<{ conv: Conversation; index: number } | null>(null); // para «Deshacer»
  const abortRef = useRef<AbortController | null>(null);
  const sendingRef = useRef(false); // envío en curso (síncrono: el estado `loadingId` llega en el siguiente render)
  const scrollRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const menuWasOpen = useRef(false);
  const positions = useRef(new Map<string, number>()); // posición de lectura de cada conversación
  const seen = useRef<{ id?: string; len: number }>({ len: 0 });

  const active = state.conversations.find((c) => c.id === state.activeId) ?? null;
  const messages = active?.messages ?? [];
  const sport: Sport = active?.sport ?? DEFAULT_SPORT; // el deporte activo es el de la conversación activa

  useEffect(() => {
    saveState(state);
  }, [state]);
  useEffect(() => trackVisualViewport(), []);

  // Desplazamiento: al preguntar, al final (se ve el estado de carga); al llegar la respuesta, al INICIO del turno; al cambiar de
  // conversación, donde se dejó (o en el último turno si es la primera vez que se abre).
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el || !active) return;
    const prev = seen.current;
    seen.current = { id: active.id, len: active.messages.length };
    if (prev.id !== active.id) {
      const saved = positions.current.get(active.id);
      if (saved !== undefined) el.scrollTop = saved;
      else scrollToLastTurn(el, false);
      return;
    }
    if (active.messages.length > prev.len) {
      const last = active.messages[active.messages.length - 1];
      if (last.role === "user") scrollBox(el, el.scrollHeight, true);
      else scrollToLastTurn(el, true);
    }
  }, [active?.id, active?.messages.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el || !active) return;
    positions.current.set(active.id, el.scrollTop);
    const far = el.scrollHeight - el.scrollTop - el.clientHeight > FAR_FROM_END;
    setFarFromEnd((f) => (f === far ? f : far));
  };

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
    // Solo con la pestaña visible: en segundo plano no se gasta red ni batería; al volver, se comprueba en el acto.
    const t = setInterval(() => { if (document.visibilityState !== "hidden") void refreshHealth(); }, HEALTH_POLL_MS);
    const onVisible = () => { if (document.visibilityState === "visible") void refreshHealth(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", onVisible); };
  }, [refreshHealth]);

  const ask = useCallback(
    async (convId: string, sport: Sport, question: string, history: HistoryItem[], focus: Focus) => {
      setLoadingId(convId);
      setSlow(false);
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const timer = setTimeout(() => setSlow(true), SLOW_MS);
      try {
        const response = await askAgent(question, convId, sport, history, focus, ctrl.signal);
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
        sendingRef.current = false;
        abortRef.current = null;
        setSlow(false);
        setLoadingId(null);
      }
    },
    [refreshHealth],
  );

  const send = (question: string) => {
    if (!active || loadingId || sendingRef.current) return; // un doble toque (p. ej. en una opción) nunca envía dos veces
    sendingRef.current = true;
    const history = historyFor(active.messages); // contexto de ESTA conversación, sin la pregunta actual
    const focus = focusFor(active.messages);
    setState((s) => appendMessage(s, active.id, { id: uid(), role: "user", content: question, createdAt: Date.now() }));
    void ask(active.id, active.sport, question, history, focus);
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
    void ask(active.id, active.sport, question, historyFor(before), focusFor(before));
  };

  const submitFeedback = async (messageId: string, runId: string | null, fb: Feedback) => {
    if (!active) return;
    await sendFeedback(active.id, messageId, runId, fb, active.sport); // si falla, la valoración no se marca como enviada
    setState((s) => setFeedback(s, active.id, messageId, fb));
  };

  // Callbacks ESTABLES para los mensajes memorizados: la versión vigente se lee de una ref (sin cierres obsoletos).
  const live = useRef({ send, retry, submitFeedback });
  live.current = { send, retry, submitFeedback };
  const stableSend = useCallback((q: string) => live.current.send(q), []);
  const stableRetry = useCallback((m: Message) => live.current.retry(m), []);
  const stableFeedback = useCallback((id: string, runId: string | null, fb: Feedback) => live.current.submitFeedback(id, runId, fb), []);
  const cancel = useCallback(() => abortRef.current?.abort(), []);
  const newConversation = useCallback(() => { setState((s) => startConversation(s, s.conversations.find((c) => c.id === s.activeId)?.sport)); setMenu(false); }, []);
  const switchSport = useCallback((next: Sport) => setState((s) => changeSport(s, next)), []);

  const loading = loadingId !== null;
  const loadingHere = loading && loadingId === active?.id;
  const lastAgentId = [...messages].reverse().find((m) => m.role === "assistant" && m.response)?.id;
  const empty = messages.length === 0;
  return (
    <CrestProvider sport={sport}>
    <div className="relative flex h-full bg-canvas">
      <Sidebar
        conversations={state.conversations}
        activeId={state.activeId}
        open={menu}
        onNew={newConversation}
        onSelect={(id) => { setState((s) => selectConversation(s, id)); setMenu(false); }}
        onDelete={(id) => {
          const index = state.conversations.findIndex((c) => c.id === id);
          if (index >= 0) setDeleted({ conv: state.conversations[index], index });
          positions.current.delete(id);
          void forgetConversation(id).catch(() => undefined); // el servidor la olvida ya; «Deshacer» la restaura en local y la siguiente pregunta resiembra el historial
          setState((s) => { const n = deleteConversation(s, id); return n.conversations.length ? n : startConversation(n); });
        }}
        onClose={() => setMenu(false)}
      />
      <main ref={mainRef} className="relative flex min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 items-center gap-1 border-b border-line bg-surface pl-[max(0.25rem,env(safe-area-inset-left))] pr-[max(0.25rem,env(safe-area-inset-right))] pt-[env(safe-area-inset-top)] md:px-4">
          <div className="flex h-header min-w-0 flex-1 items-center gap-1">
            <button ref={menuButtonRef} onClick={() => setMenu(true)} aria-label="Abrir conversaciones" aria-haspopup="dialog" aria-expanded={menu}
              className="grid h-touch w-touch shrink-0 place-items-center rounded-md text-ink hover:bg-surface-2 md:hidden">
              <Menu size={22} aria-hidden="true" />
            </button>
            <span className="flex min-w-0 items-center gap-2 md:hidden">
              <LogoMark className="h-7 w-7 shrink-0" />
              <h1 className="font-display text-[19px] font-extrabold tracking-tight text-brand-text stretch-brand">Futsito</h1>
            </span>
            {/* Escritorio: la marca vive en el panel lateral; la cabecera orienta con el título de la conversación. */}
            <p className="hidden min-w-0 truncate text-[15px] font-semibold text-ink md:block" data-testid="conversation-title">
              {empty ? "Nueva conversación" : active?.title}
            </p>
          </div>
          <ConnectionBadge connection={connection} onRefresh={() => void refreshHealth()} />
          <button onClick={newConversation} aria-label="Nueva consulta" title="Nueva conversación" disabled={empty}
            className="grid h-touch w-touch shrink-0 place-items-center rounded-md text-ink hover:bg-surface-2 disabled:text-ink-2 disabled:opacity-40 md:hidden">
            <SquarePen size={20} aria-hidden="true" />
          </button>
        </header>
        <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto overflow-x-hidden overscroll-contain" data-testid="messages">
          <div className={`mx-auto flex min-h-full max-w-3xl flex-col px-3 min-[360px]:px-4 ${empty ? "" : "gap-6 pb-6 pt-4"}`}>
            {active && empty && <Welcome onPick={stableSend} disabled={loading} sport={sport} />}
            {messages.map((m, i) => (
              <MessageBubble key={m.id} message={m} onRetry={m.failure && i === messages.length - 1 ? stableRetry : undefined}
                onSuggest={m.id === lastAgentId ? stableSend : undefined} suggestDisabled={loading} onFeedback={stableFeedback} />
            ))}
            {loadingHere && <AgentStatus slow={slow} onCancel={cancel} />}
          </div>
        </div>
        <div className="relative">
          {farFromEnd && !empty && (
            <button type="button" onClick={() => scrollRef.current && scrollBox(scrollRef.current, scrollRef.current.scrollHeight, true)}
              aria-label="Ir al final de la conversación" data-testid="jump-to-end"
              className="absolute -top-14 left-1/2 z-[5] grid h-10 w-10 -translate-x-1/2 place-items-center rounded-full border border-line bg-surface text-ink shadow-pop hover:bg-surface-2">
              <ArrowDown size={18} aria-hidden="true" />
            </button>
          )}
          <Composer onSend={stableSend} onStop={loadingHere ? cancel : undefined} loading={loading}
            focus={historyFor(messages).length > 0 ? focusFor(messages) : undefined} onClearContext={newConversation}
            sportSwitch={<SportSwitch sport={sport} onChange={switchSport} />} />
        </div>
      </main>
      {deleted && (
        <UndoToast key={deleted.conv.id} message={`Conversación «${deleted.conv.title}» borrada`} onClose={() => setDeleted(null)}
          onUndo={() => { const d = deleted; setDeleted(null); setState((s) => restoreConversation(s, d.conv, d.index)); }} />
      )}
    </div>
    </CrestProvider>
  );
}
