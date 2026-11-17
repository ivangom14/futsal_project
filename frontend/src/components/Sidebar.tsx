import { useEffect, useMemo, useRef, useState } from "react";
import { MoreHorizontal, Search, SquarePen, Trash2, X } from "lucide-react";
import { LogoMark } from "./Logo";
import ThemeSwitch from "./ThemeSwitch";
import type { Conversation } from "../types";

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
/** A partir de cuántas conversaciones se ofrece la búsqueda (con pocas, la lista se abarca de un vistazo). */
export const SEARCH_FROM = 6;

const DAY = 86_400_000;
const startOfDay = (t: number) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };

/** Grupos temporales por última actividad: Hoy, Ayer, Últimos 7 días, Últimos 30 días, Anteriores (vacíos, fuera). */
export function groupByRecency(items: Conversation[], now = Date.now()): { label: string; items: Conversation[] }[] {
  const today = startOfDay(now);
  const bounds: [string, number][] = [["Hoy", today], ["Ayer", today - DAY], ["Últimos 7 días", today - 7 * DAY], ["Últimos 30 días", today - 30 * DAY], ["Anteriores", -Infinity]];
  const groups = bounds.map(([label]) => ({ label, items: [] as Conversation[] }));
  for (const c of [...items].sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0))) {
    const t = c.updatedAt ?? c.createdAt ?? 0;
    groups[bounds.findIndex(([, from]) => t >= from)].items.push(c);
  }
  return groups.filter((g) => g.items.length > 0);
}

const fold = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

/** Coincide si TODAS las palabras buscadas aparecen en el título o en alguna pregunta del usuario (sin tildes ni mayúsculas). */
export function matchesQuery(c: Conversation, q: string): boolean {
  const words = fold(q).split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const hay = fold([c.title, ...c.messages.filter((m) => m.role === "user").map((m) => m.content)].join(" \n "));
  return words.every((w) => hay.includes(w));
}

/** Menú de acciones de una conversación (hoy, solo borrar: con «Deshacer» en el aviso). Escape o un toque fuera lo cierran. */
function RowMenu({ title, onDelete }: { title: string; onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    box.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onDown = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); setOpen(false); trigger.current?.focus(); }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => { document.removeEventListener("pointerdown", onDown); document.removeEventListener("keydown", onKey, true); };
  }, [open]);
  return (
    <div ref={box} className="relative shrink-0">
      <button ref={trigger} type="button" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}
        aria-label={`Acciones de la conversación ${title}`} title="Más acciones"
        className="row-actions grid h-9 w-9 place-items-center rounded-md text-ink-2 hover:bg-surface hover:text-ink [@media(pointer:coarse)]:h-touch [@media(pointer:coarse)]:w-10">
        <MoreHorizontal size={18} aria-hidden="true" />
      </button>
      {open && (
        <div role="menu" aria-label={`Acciones de ${title}`} className="absolute right-0 top-full z-30 mt-1 min-w-44 rounded-md border border-line bg-surface p-1 shadow-pop">
          <button role="menuitem" type="button" onClick={() => { setOpen(false); onDelete(); }} aria-label={`Borrar conversación ${title}`}
            className="flex min-h-10 w-full items-center gap-2 rounded-sm px-2.5 text-left text-sm text-danger hover:bg-danger-soft focus-visible:bg-danger-soft">
            <Trash2 size={16} strokeWidth={1.75} aria-hidden="true" /> Borrar
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * Historial. En móvil abierto es un diálogo modal: foco al primer control, Tab/Shift+Tab no salen del panel, Escape o el fondo lo
 * cierran (App devuelve el foco al botón del menú). Al pasar a escritorio con el cajón abierto, se cierra.
 */
export default function Sidebar({ conversations, activeId, open, onNew, onSelect, onDelete, onClose }: Props) {
  const ref = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose; // App pasa una función nueva en cada render: el efecto no debe reiniciarse (movería el foco)
  const [query, setQuery] = useState("");

  useEffect(() => {
    const panel = ref.current;
    if (!open || !panel) return;
    const focusables = () => Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
    (panel.querySelector<HTMLElement>("[data-autofocus]") ?? focusables()[0])?.focus();
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

  // La conversación vacía (recién creada) no se lista: «Nueva conversación» ya la representa.
  const listed = conversations.filter((c) => c.messages.length > 0);
  const groups = useMemo(() => groupByRecency(listed.filter((c) => matchesQuery(c, query))), [listed, query]);
  const searchable = listed.length >= SEARCH_FROM;

  return (
    <>
      {open && <div className="fixed inset-0 z-10 bg-scrim md:hidden" onClick={onClose} aria-hidden="true" data-testid="drawer-backdrop" />}
      <aside
        ref={ref}
        {...(open ? { role: "dialog", "aria-modal": true } : {})}
        className={`${open ? "flex" : "hidden"} absolute inset-y-0 left-0 z-20 w-[min(20rem,86vw)] flex-col border-r border-line bg-surface pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] pl-[env(safe-area-inset-left)] shadow-pop md:static md:flex md:w-72 md:shadow-none`}
        aria-label="Conversaciones"
      >
        <div className="flex h-header shrink-0 items-center gap-2 pl-4 pr-1">
          <LogoMark className="h-8 w-8" />
          <span className="flex-1 font-display text-xl font-extrabold tracking-tight text-brand-text stretch-brand">Futsito</span>
          <button onClick={onClose} aria-label="Cerrar menú" className="grid h-touch w-touch place-items-center rounded-md text-ink-2 hover:bg-surface-2 md:hidden">
            <X size={20} aria-hidden="true" />
          </button>
        </div>
        <div className="space-y-2 px-3 pb-2">
          <button onClick={onNew} data-autofocus className="flex min-h-touch w-full items-center gap-2.5 rounded-md bg-brand px-3 text-[15px] font-semibold text-brand-on hover:bg-brand-hover md:min-h-10">
            <SquarePen size={18} aria-hidden="true" /> Nueva conversación
          </button>
          {searchable && (
            <label className="flex min-h-10 items-center gap-2 rounded-md border border-line bg-canvas px-2.5 text-ink-2 focus-within:border-focus">
              <Search size={16} aria-hidden="true" className="shrink-0" />
              <span className="sr-only">Buscar conversaciones</span>
              <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Buscar"
                className="min-w-0 flex-1 bg-transparent py-1.5 text-base text-ink focus:outline-none focus-visible:outline-none md:text-sm" />
            </label>
          )}
        </div>
        <nav className="flex-1 overflow-y-auto overscroll-contain px-2 pb-3" aria-label="Historial de conversaciones">
          {groups.map((g) => (
            <section key={g.label} className="mt-2 first:mt-0" aria-labelledby={`grp-${g.label}`}>
              <h2 id={`grp-${g.label}`} className="px-2 pb-1 pt-2 text-xs font-semibold text-ink-2">{g.label}</h2>
              <ul className="space-y-px">
                {g.items.map((c) => {
                  const current = c.id === activeId;
                  return (
                    <li key={c.id} className={`conv-row group relative flex items-center rounded-md ${current ? "bg-surface-2" : "hover:bg-surface-2"}`}>
                      {current && <span aria-hidden="true" className="absolute inset-y-2 left-0 w-[3px] rounded-full bg-brand" />}
                      <button onClick={() => onSelect(c.id)} aria-current={current ? "page" : undefined}
                        className={`min-h-10 min-w-0 flex-1 truncate rounded-md py-2 pl-3 pr-1 text-left text-sm [@media(pointer:coarse)]:min-h-touch ${current ? "font-semibold text-ink" : "text-ink"}`}>
                        {c.title}
                      </button>
                      <RowMenu title={c.title} onDelete={() => onDelete(c.id)} />
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
          {listed.length === 0 && <p className="px-3 py-4 text-sm text-ink-2">Tus conversaciones aparecerán aquí.</p>}
          {listed.length > 0 && groups.length === 0 && <p className="px-3 py-4 text-sm text-ink-2" role="status">Ninguna conversación coincide con «{query}».</p>}
        </nav>
        <div className="border-t border-line p-3">
          <ThemeSwitch />
        </div>
      </aside>
    </>
  );
}
