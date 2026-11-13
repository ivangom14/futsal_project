import type { ButtonHTMLAttributes } from "react";

/**
 * Chips pulsables (guía §F.10): `suggest` para preguntas sugeridas (tono marca suave) y `option` para elegir una opción
 * de una aclaración (superficie con borde). 40 px de alto; el texto puede ocupar varias líneas.
 */
export type ChipVariant = "suggest" | "option";

const VARIANT: Record<ChipVariant, string> = {
  suggest: "border-line bg-brand-soft text-brand-text hover:border-brand",
  option: "border-line-strong bg-surface text-ink hover:bg-surface-2",
};

export default function Chip({ variant = "suggest", className = "", type = "button", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ChipVariant }) {
  return (
    <button type={type}
      className={`min-h-10 rounded-full border px-3 py-2 text-left text-sm disabled:cursor-not-allowed disabled:opacity-50 ${VARIANT[variant]} ${className}`.trim()}
      {...rest} />
  );
}
