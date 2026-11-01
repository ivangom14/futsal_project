import type { ChatResponse } from "../types";

const sec = (ms: number) => `${(ms / 1000).toFixed(1)} s`;

export default function TechnicalDetails({ response }: { response: ChatResponse }) {
  const d = response.diagnostics;
  return (
    <details className="mt-2 text-xs text-slate-500">
      <summary className="cursor-pointer select-none">Detalles técnicos</summary>
      <div className="mt-1 space-y-1 rounded border border-slate-200 bg-white p-2">
        {d.llm_ms !== undefined && (
          <p data-testid="time-breakdown">
            Tiempo: modelo {sec(d.llm_ms)}{(d.llm_retries ?? 0) > 0 && <> (de ellos {sec(d.retry_wait_ms ?? 0)} esperando {d.llm_retries} reintento(s) por límite del servicio)</>}
            {" "}· herramientas {sec(d.tools_ms ?? 0)} · contexto {sec(d.context_ms ?? 0)} · montaje {sec(d.postprocess_ms ?? 0)}
            {d.input_tokens_peak ? <> · entrada máx. {d.input_tokens_peak} tokens</> : null}
          </p>
        )}
        <p>
          {(d.duration_ms / 1000).toFixed(1)} s · {d.llm_calls} llamadas al modelo · {d.tool_calls} herramientas · {d.history_turns} turnos de contexto · tokens {d.input_tokens} entrada / {d.output_tokens} salida ({d.token_source})
        </p>
        <p>
          Modelo: {d.provider ?? "?"}/{d.model ?? "?"} · run <code>{d.run_id}</code>
          {response.guardrail && <> · guardarraíl: <code>{response.guardrail}</code></>}
        </p>
        {d.resolved_scope && (
          <p>
            Ámbito interpretado: {d.resolved_scope.filters}
            {d.resolved_scope.inherited && " (heredado de la conversación)"} → {d.resolved_scope.competitions.join("; ") || "sin competiciones"}
            {d.resolved_scope.total > d.resolved_scope.competitions.length && ` y ${d.resolved_scope.total - d.resolved_scope.competitions.length} más`}
          </p>
        )}
        {d.resolved_question && <p>Interpretada como: <em>{d.resolved_question}</em></p>}
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
        {response.error && <p className="text-red-600">Ejecución incompleta: {response.error}</p>}
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
