import { showDiagnostics } from "../diagnostics";
import type { Connection } from "../types";

export function describe(c: Connection): { label: string; tone: string; title: string } {
  switch (c.state) {
    case "checking":
      return { label: "Comprobando…", tone: "bg-ink-2", title: "Comprobando conexión" };
    case "offline":
      return { label: "Sin conexión", tone: "bg-danger", title: c.detail };
    case "ok":
      return { label: "Conectado", tone: "bg-success", title: "Agente, modelo y API de datos operativos" };
    case "degraded": {
      const h = c.health;
      const problems = [
        !h.agent.ready && `agente no iniciado${h.agent.error ? ` (${h.agent.error})` : ""}`,
        !h.llm.key_configured && `falta la clave del modelo (${h.llm.provider})`,
        !h.data_api.reachable && `API de datos no responde${h.data_api.detail ? ` (${h.data_api.detail})` : ""}`,
      ].filter(Boolean);
      return { label: "Servicio degradado", tone: "bg-warning", title: problems.join("; ") };
    }
  }
}

/**
 * Estado del servicio en la cabecera: SOLO cuando hay un problema (sin conexión o degradado), como aviso compacto que permite
 * reintentar. «Conectado» y «Comprobando…» no aportan nada al usuario y no se muestran (salvo con diagnóstico activado).
 */
export default function ConnectionBadge({ connection, onRefresh }: { connection: Connection; onRefresh: () => void }) {
  const d = describe(connection);
  const problem = connection.state === "offline" || connection.state === "degraded";
  const diagnostics = showDiagnostics();
  if (!problem && !diagnostics) return <span className="sr-only" role="status">{connection.state === "ok" ? "Servicio disponible" : ""}</span>;
  return (
    <button onClick={onRefresh} title={`${d.title} · Pulsa para comprobar de nuevo`} aria-label={`Estado: ${d.label}. ${d.title}`}
      className={`flex min-h-9 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium ${problem ? (connection.state === "offline" ? "bg-danger-soft text-danger" : "bg-warning-soft text-warning") : "text-ink-2 hover:bg-surface-2"}`}>
      <span className={`h-2 w-2 shrink-0 rounded-full ${d.tone}`} aria-hidden="true" />
      {d.label}
      {diagnostics && (connection.state === "ok" || connection.state === "degraded") && connection.health.context_mode && (
        <span data-testid="context-mode" className="text-xs text-ink-2">· {connection.health.context_mode}</span>
      )}
    </button>
  );
}
