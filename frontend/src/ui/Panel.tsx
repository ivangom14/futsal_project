import type { ReactNode } from "react";

/**
 * Contenedor ÚNICO de los visuales deportivos: superficie con borde fino, sin sombra ni cabecera coloreada (una tarjeta, nunca
 * tarjetas dentro de tarjetas). El título del backend («Clasificación · PRIMERA … · Grupo 2») se lee como titular + antetítulo:
 * lo primero es qué es, lo segundo de qué competición.
 */
export function splitTitle(title: string): [string, string | null] {
  const [head, ...rest] = title.split(" · ");
  return [head, rest.length ? rest.join(" · ") : null];
}

interface Props {
  title: string;
  subtitle?: string | null;
  /** Dato breve a la derecha del título (p. ej. «10 de 48»). */
  aside?: ReactNode;
  /** Nota bajo el título (datos parciales, avisos). */
  note?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  className?: string;
  testId?: string;
}

export default function Panel({ title, subtitle, aside, note, footer, children, className = "", testId = "visual" }: Props) {
  const [head, kicker] = splitTitle(title);
  return (
    <figure className={`panel mt-3 overflow-hidden rounded-lg border border-line bg-surface ${className}`.trim()} data-testid={testId}>
      <figcaption className="flex items-start gap-3 border-b border-line px-3 pb-2 pt-2.5">
        <div className="min-w-0 flex-1">
          {kicker && <p className="text-[11px] font-semibold uppercase leading-4 tracking-wide text-ink-2 [overflow-wrap:anywhere]">{kicker}</p>}
          <p className="font-display text-[15px] font-bold leading-5 text-ink">{head}</p>
          {subtitle && <p className="text-xs leading-4 text-ink-2">{subtitle}</p>}
          {note}
        </div>
        {aside && <div className="shrink-0 pt-0.5 text-xs text-ink-2">{aside}</div>}
      </figcaption>
      {children}
      {footer && <div className="border-t border-line">{footer}</div>}
    </figure>
  );
}

/** Pie de acción de un panel («Ver más»): botón de ancho completo, objetivo táctil cómodo. */
export function PanelAction({ onClick, disabled, children, testId }: { onClick: () => void; disabled?: boolean; children: ReactNode; testId?: string }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} data-testid={testId}
      className="flex min-h-touch w-full items-center justify-center gap-1.5 px-3 text-sm font-semibold text-accent hover:bg-surface-2 disabled:cursor-not-allowed disabled:text-ink-2">
      {children}
    </button>
  );
}

/** Nota de pie (sin acción): cuántos se muestran y cómo acotar. */
export function PanelNote({ children }: { children: ReactNode }) {
  return <p className="px-3 py-2 text-xs text-ink-2">{children}</p>;
}
