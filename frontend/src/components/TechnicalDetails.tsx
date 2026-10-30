import type { ChatResponse } from "../types";

export default function TechnicalDetails({ response }: { response: ChatResponse }) {
  const d = response.diagnostics;
  return (
    <details className="mt-2 text-xs text-slate-500">
      <summary className="cursor-pointer select-none">Detalles técnicos</summary>
      <div className="mt-1 space-y-1 rounded border border-slate-200 bg-white p-2">
        <p>
          {(d.duration_ms / 1000).toFixed(1)} s · {d.llm_calls} llamadas al modelo · {d.tool_calls} herramientas · {d.history_turns} turnos de contexto · tokens {d.input_tokens} entrada / {d.output_tokens} salida ({d.token_source})
        </p>
        <p>
          Modelo: {d.provider ?? "?"}/{d.model ?? "?"} · run <code>{d.run_id}</code>
          {response.guardrail && <> · guardarraíl: <code>{response.guardrail}</code></>}
        </p>
        {d.tools.length > 0 && (
          <ul className="space-y-0.5">
            {d.tools.map((t, i) => (
              <li key={i} className={t.is_error ? "text-red-600" : ""}>
                <code>{t.name}</code>({JSON.stringify(t.arguments)}) · {Math.round(t.duration_ms)} ms · {t.summary}
                {t.refused && " · rechazada por el guardarraíl"}
              </li>
            ))}
          </ul>
        )}
        {d.errors.length > 0 && (
          <ul className="text-red-600">
            {d.errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
}
