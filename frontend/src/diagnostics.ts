/**
 * ¿Mostrar información de diagnóstico (detalles técnicos de cada respuesta, modo de contexto en la cabecera)?
 * Oculta por defecto para el usuario final. Se activa con VITE_SHOW_DIAGNOSTICS=1 (.env), con `?debug` en la URL
 * o con localStorage `futsito.debug` = "1". Las pruebas la activan en vite.config.ts.
 */
export function showDiagnostics(): boolean {
  if (import.meta.env.VITE_SHOW_DIAGNOSTICS === "1") return true;
  try {
    return new URLSearchParams(window.location.search).has("debug") || localStorage.getItem("futsito.debug") === "1";
  } catch {
    return false;
  }
}
