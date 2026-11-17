import type { ButtonHTMLAttributes } from "react";

/**
 * Chips pulsables: `suggest` para preguntas sugeridas y `option` para elegir una opción de una aclaración (más marcada: es la
 * respuesta que Futsito espera). 40 px de alto (44 en táctil); el texto puede ocupar varias líneas.
 */
export type ChipVariant = "suggest" | "option";

const VARIANT: Record<ChipVariant, string> = {
  suggest: "rounded-[20px] border-line bg-surface text-ink hover:border-line-strong hover:bg-surface-2",
  option: "rounded-md border-accent bg-accent-soft text-ink hover:bg-surface",
};

export default function Chip({ variant = "suggest", className = "", type = "button", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ChipVariant }) {
  return (
    <button type={type}
      className={`inline-flex min-h-10 items-center gap-1.5 border px-3.5 py-1.5 text-left text-sm leading-snug transition-colors disabled:cursor-not-allowed disabled:opacity-50 [@media(pointer:coarse)]:min-h-touch ${VARIANT[variant]} ${className}`.trim()}
      {...rest} />
  );
}
