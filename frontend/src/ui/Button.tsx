import type { ButtonHTMLAttributes } from "react";

/** Botones del sistema «Pista» (guía §F.3). Alto mínimo 40 px (objetivo táctil); el foco lo pinta el estilo global. */
export type ButtonVariant = "primary" | "secondary" | "ghost";

const VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-brand text-brand-on hover:bg-brand-hover",
  secondary: "border border-line-strong bg-surface text-ink hover:bg-surface-2",
  ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
};

export function buttonClass(variant: ButtonVariant = "secondary", extra = ""): string {
  return `inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg px-3 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${VARIANT[variant]} ${extra}`.trim();
}

export default function Button({ variant = "secondary", className = "", type = "button", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return <button type={type} className={buttonClass(variant, className)} {...rest} />;
}
