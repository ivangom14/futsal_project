import { useEffect, useRef } from "react";
import FutsitoLoader from "./FutsitoLoader";
import Button from "../ui/Button";
import Notice from "../ui/Notice";

/**
 * Segundos desde que empezó la consulta; se enseñan a partir de 5 s (antes serían ruido). Se escriben directamente en el DOM:
 * nada de re-renderizar React cada segundo. No se anuncian (aria-hidden): cambian cada segundo.
 */
function Elapsed() {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const t0 = Date.now();
    const t = setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000);
      if (ref.current) ref.current.textContent = s >= 5 ? ` · ${s} s` : "";
    }, 1000);
    return () => clearInterval(t);
  }, []);
  return <span ref={ref} aria-hidden="true" className="tabular-nums text-ink-2" data-testid="elapsed" />;
}

/**
 * Estado «Futsito está trabajando» (guía §F.1 `AgentStatus`): loader con identidad, texto honesto (sin pasos inventados:
 * el backend no informa del progreso), tiempo transcurrido, aviso si tarda más de lo normal y cancelar.
 */
export default function AgentStatus({ slow, onCancel }: { slow: boolean; onCancel: () => void }) {
  return (
    <div className="flex items-start gap-3" data-testid="agent-status">
      <FutsitoLoader className="h-14 w-14 text-ink" />
      <div className="min-w-0 flex-1 pt-1">
        <p role="status" className="text-sm text-ink-2">
          Futsito está consultando los datos oficiales…<Elapsed />
        </p>
        {slow && (
          <Notice tone="warning" className="mt-2" testId="slow-hint">
            Esto está tardando más de lo normal. Puede que el servicio de IA esté limitando las peticiones; puedes esperar o cancelar.
          </Notice>
        )}
        <Button onClick={onCancel} className="mt-2">Cancelar</Button>
      </div>
    </div>
  );
}
