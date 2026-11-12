import type { SVGProps } from "react";

/**
 * Iconos propios que Lucide no tiene, dibujados en su misma rejilla (24 px, trazo 2, extremos redondeados) y con su misma API
 * básica (`size`, `strokeWidth`, `className`). Guía: docs/design-system/03-guia-estilos.md §D.
 */
type IconProps = Omit<SVGProps<SVGSVGElement>, "ref"> & { size?: number };

/** Balón de fútbol sala (paneles simplificados). Decorativo por defecto. */
export function FutsalBall({ size = 24, strokeWidth = 2, ...rest }: IconProps) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...rest}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7.5l3.8 2.8-1.5 4.4H9.7l-1.5-4.4z" />
      <path d="M12 3v4.5M20.6 9.4l-4.8.9M17.3 19.3l-3-4.6M6.7 19.3l3-4.6M3.4 9.4l4.8.9" />
    </svg>
  );
}
