import type { Connection } from "../types";

export function describe(c: Connection): { label: string; tone: string; title: string } {
  switch (c.state) {
    case "checking":
      return { label: "Comprobando…", tone: "bg-slate-400", title: "Comprobando conexión" };
    case "offline":
      return { label: "Sin conexión", tone: "bg-red-500", title: c.detail };
    case "ok":
      return { label: "Conectado", tone: "bg-green-500", title: "Agente, modelo y API de datos operativos" };
    case "degraded": {
      const h = c.health;
      const problems = [
        !h.agent.ready && `agente no iniciado${h.agent.error ? ` (${h.agent.error})` : ""}`,
        !h.llm.key_configured && `falta la clave del modelo (${h.llm.provider})`,
        !h.data_api.reachable && `API de datos no responde${h.data_api.detail ? ` (${h.data_api.detail})` : ""}`,
      ].filter(Boolean);
      return { label: "Servicio degradado", tone: "bg-amber-500", title: problems.join("; ") };
    }
  }
}

export default function ConnectionBadge({ connection, onRefresh }: { connection: Connection; onRefresh: () => void }) {
  const d = describe(connection);
  return (
    <button onClick={onRefresh} title={d.title} aria-label={`Estado: ${d.label}. ${d.title}`} className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1 text-xs hover:bg-slate-50">
      <span className={`h-2 w-2 rounded-full ${d.tone}`} />
      {d.label}
    </button>
  );
}
