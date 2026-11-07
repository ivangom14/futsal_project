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
        {d.context && (
          <div data-testid="context-info">
            <p>
              Comprensión ({d.context.mode}): <strong>{d.context.status ?? "—"}</strong>
              {d.context.intent && <> · intención <code>{d.context.intent}</code></>}
              {d.context.competitions && d.context.competitions.length > 0 && <> → {d.context.competitions.join("; ")}</>}
              {d.context.team && <> · equipo {d.context.team}</>}
              {d.context.provenance?.scope && <> · ámbito {d.context.provenance.scope}</>}
              {d.context.state && <> · estado {d.context.state === "rebuilt" ? "reconstruido del historial" : "guardado"}</>}
            </p>
            {d.context.reason && <p>Motivo: {d.context.reason}</p>}
            {d.context.options && d.context.options.length > 0 && <p>Opciones ofrecidas: {d.context.options.join("; ")}</p>}
            {d.context.assumption && <p>Suposición declarada: {d.context.assumption}</p>}
            {d.context.interpreter_trigger && <p>Intérprete LLM consultado ({d.context.interpreter_trigger}){d.context.interpreter?.competitions?.length ? <> → {d.context.interpreter.competitions.join("; ")}</> : null}{d.context.interpreter_error && <> · falló: {d.context.interpreter_error}</>}</p>}
            {d.context.recovery !== undefined && <p>Recuperación: <code>{String(d.context.recovery)}</code></p>}
            {d.context.error && <p className="text-amber-700">La comprensión de contexto falló ({d.context.error}): se usó el flujo legacy.</p>}
          </div>
        )}
        {d.resolved_question && <p>Interpretada como: <em>{d.resolved_question}</em></p>}
        {d.reconciliation && (
          <div data-testid="reconciliation">
            <p>
              Conciliación con los gráficos: {d.reconciliation.removed} de {d.reconciliation.considered} elementos de lista o tabla se omitieron por repetir un gráfico
              {d.reconciliation.kept > 0 && <>; {d.reconciliation.kept} se conservaron</>}
              {d.reconciliation.pointer_added && " (se añadió una frase que remite al gráfico)"}.
            </p>
            {d.reconciliation.error && <p className="text-amber-700">La conciliación falló ({d.reconciliation.error}): se muestra el texto original del agente.</p>}
            {d.reconciliation.removed_items.length > 0 && (
              <ul className="list-disc pl-4">
                {d.reconciliation.removed_items.map((r, i) => <li key={`r${i}`}>Omitido: «{r.snippet}» · {r.reason}</li>)}
              </ul>
            )}
            {d.reconciliation.kept_uncertain.length > 0 && (
              <ul className="list-disc pl-4">
                {d.reconciliation.kept_uncertain.map((r, i) => <li key={`k${i}`}>Conservado: «{r.snippet}» · {r.reason}{r.left.length > 0 && <> (no respaldado: {r.left.join(", ")})</>}</li>)}
              </ul>
            )}
          </div>
        )}
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
