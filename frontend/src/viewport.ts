/**
 * Teclado virtual (iOS Safari no redimensiona el viewport de diseño al abrirlo): mientras un campo de texto tiene el foco y el
 * viewport VISUAL es más bajo que la ventana, el alto de la app pasa a ser el visual (`--app-h`) y el compositor queda pegado al
 * teclado. Sin teclado (o con zoom manual) se retira y manda `100dvh`. Chrome Android ya lo resuelve con `interactive-widget`.
 */
export function trackVisualViewport(): () => void {
  const vv = typeof window !== "undefined" ? window.visualViewport : null;
  if (!vv) return () => undefined;
  const root = document.documentElement;
  let frame = 0;
  const update = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const el = document.activeElement;
      const typing = el instanceof HTMLTextAreaElement || (el instanceof HTMLInputElement && el.type !== "checkbox" && el.type !== "radio");
      const keyboard = typing && Math.abs(vv.scale - 1) < 0.01 && vv.height < window.innerHeight - 80;
      if (keyboard) {
        root.style.setProperty("--app-h", `${Math.round(vv.height)}px`);
        if (window.scrollY !== 0) window.scrollTo(0, 0);
      } else root.style.removeProperty("--app-h");
    });
  };
  vv.addEventListener("resize", update);
  vv.addEventListener("scroll", update);
  document.addEventListener("focusin", update);
  document.addEventListener("focusout", update);
  return () => {
    cancelAnimationFrame(frame);
    vv.removeEventListener("resize", update);
    vv.removeEventListener("scroll", update);
    document.removeEventListener("focusin", update);
    document.removeEventListener("focusout", update);
  };
}
