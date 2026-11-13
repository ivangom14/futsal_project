import { useEffect, useRef } from "react";
import { Trash2, X } from "lucide-react";
import { LogoMark } from "./Logo";
import ThemeSwitch from "./ThemeSwitch";
import type { Conversation } from "../types";
import { buttonClass } from "../ui/Button";

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  /** Solo en móvil: el panel se abre como cajón modal sobre el chat. En escritorio siempre está visible. */
  open: boolean;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onClose: () => void;
}

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * Lista de conversaciones. En móvil abierta es un diálogo modal: foco al primer control, Tab/Shift+Tab no salen del panel,
 * Escape o el fondo lo cierran (App devuelve el foco al botón del menú). Al pasar a escritorio con el cajón abierto, se cierra.
 */
export default function Sidebar({ conversations, activeId, open, onNew, onSelect, onDelete, onClose }: Props) {
  const ref = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose; // App pasa una función nueva en cada render: el efecto no debe reiniciarse (movería el foco)

  useEffect(() => {
    const panel = ref.current;
    if (!open || !panel) return;
    const focusables = () => Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
    focusables()[0]?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    const desktop = typeof window.matchMedia === "function" ? window.matchMedia("(min-width: 768px)") : null;
    const onResize = () => { if (desktop?.matches) closeRef.current(); };
    desktop?.addEventListener?.("change", onResize);
    return () => {
      document.removeEventListener("keydown", onKey);
      desktop?.removeEventListener?.("change", onResize);
    };
  }, [open]);

  return (
    <>
      {open && <div className="fixed inset-0 z-10 bg-scrim md:hidden" onClick={onClose} aria-hidden="true" data-testid="drawer-backdrop" />}
      <aside
        ref={ref}
        {...(open ? { role: "dialog", "aria-modal": true } : {})}
        className={`${open ? "flex" : "hidden"} absolute inset-y-0 left-0 z-20 w-[min(18rem,85vw)] flex-col border-r border-line bg-surface shadow-lg md:static md:flex md:w-72 md:shadow-none`}
        aria-label="Conversaciones"
      >
        <div className="flex items-center gap-2 px-4 pt-3">
          <LogoMark className="h-9 w-9" />
          <span className="font-display text-xl font-extrabold tracking-tight text-brand-text stretch-brand">Futsito</span>
        </div>
        <div className="flex items-center gap-2 p-3">
          <button onClick={onNew} className={buttonClass("primary", "flex-1")}>
            + Nueva conversación
          </button>
          <button onClick={onClose} aria-label="Cerrar menú" className="grid h-10 w-10 place-items-center rounded-lg text-ink-2 hover:bg-surface-2 md:hidden">
            <X size={20} aria-hidden="true" />
          </button>
        </div>
        <ul className="flex-1 space-y-1 overflow-y-auto px-2 pb-3">
          {conversations.map((c) => (
            <li key={c.id} className={`group flex items-center rounded-lg ${c.id === activeId ? "bg-brand-soft" : "hover:bg-surface-2"}`}>
              <button onClick={() => onSelect(c.id)} aria-current={c.id === activeId} className="min-h-10 flex-1 truncate rounded-lg px-3 py-2 text-left text-sm">
                {c.title}
              </button>
              <button onClick={() => onDelete(c.id)} aria-label={`Borrar conversación ${c.title}`} title="Borrar conversación"
                className="grid h-10 w-10 shrink-0 place-items-center rounded-lg text-ink-2 hover:text-danger">
                <Trash2 size={16} strokeWidth={1.75} aria-hidden="true" />
              </button>
            </li>
          ))}
          {conversations.length === 0 && <li className="px-3 py-2 text-sm text-ink-2">Sin conversaciones</li>}
        </ul>
        <div className="border-t border-line p-3">
          <ThemeSwitch />
        </div>
      </aside>
    </>
  );
}
