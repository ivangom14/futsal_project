import { useEffect, useRef } from "react";

/** Aviso temporal con «Deshacer» (guía §F.11). El texto va en un solo nodo para que se lea de una vez. Desaparece solo. */
export default function UndoToast({ message, onUndo, onClose, ms = 6000 }: { message: string; onUndo: () => void; onClose: () => void; ms?: number }) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const t = setTimeout(() => closeRef.current(), ms);
    return () => clearTimeout(t);
  }, [message, ms]);
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-28 z-30 flex justify-center px-4">
      <div role="status" className="pointer-events-auto flex max-w-md items-center gap-3 rounded-xl bg-inverse py-1.5 pl-4 pr-1.5 text-sm text-inverse-on shadow-lg" data-testid="undo-toast">
        <span className="min-w-0">{message}</span>
        <button type="button" onClick={onUndo} className="min-h-10 shrink-0 rounded-lg px-3 font-semibold underline underline-offset-2">
          Deshacer
        </button>
      </div>
    </div>
  );
}
