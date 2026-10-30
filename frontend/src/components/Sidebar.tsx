import type { Conversation } from "../types";

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  open: boolean;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onClose: () => void;
}

export default function Sidebar({ conversations, activeId, open, onNew, onSelect, onDelete, onClose }: Props) {
  return (
    <aside className={`${open ? "flex" : "hidden"} absolute inset-y-0 left-0 z-20 w-72 flex-col border-r border-slate-200 bg-white md:static md:flex`} aria-label="Conversaciones">
      <div className="flex items-center gap-2 p-3">
        <button onClick={onNew} className="flex-1 rounded-lg bg-pitch-600 px-3 py-2 text-sm font-medium text-white hover:bg-pitch-700">
          + Nueva conversación
        </button>
        <button onClick={onClose} aria-label="Cerrar menú" className="rounded p-2 text-slate-500 md:hidden">✕</button>
      </div>
      <ul className="flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {conversations.map((c) => (
          <li key={c.id} className={`group flex items-center rounded-lg ${c.id === activeId ? "bg-pitch-50" : "hover:bg-slate-50"}`}>
            <button onClick={() => onSelect(c.id)} aria-current={c.id === activeId} className="flex-1 truncate px-3 py-2 text-left text-sm">
              {c.title}
            </button>
            <button onClick={() => onDelete(c.id)} aria-label={`Borrar conversación ${c.title}`} className="px-2 text-slate-400 hover:text-red-600">🗑</button>
          </li>
        ))}
        {conversations.length === 0 && <li className="px-3 py-2 text-sm text-slate-400">Sin conversaciones</li>}
      </ul>
    </aside>
  );
}
