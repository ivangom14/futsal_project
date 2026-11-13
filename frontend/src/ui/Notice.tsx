import type { ReactNode } from "react";
import { CircleAlert, Info, TriangleAlert } from "lucide-react";

/** Aviso en línea (guía §F.11): icono + texto, nunca solo color. */
export type NoticeTone = "info" | "warning" | "danger";

const TONE: Record<NoticeTone, string> = {
  info: "bg-accent-soft text-accent",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
};
const ICON = { info: Info, warning: TriangleAlert, danger: CircleAlert } as const;

interface Props { tone?: NoticeTone; children: ReactNode; className?: string; role?: "status" | "alert"; testId?: string }

export default function Notice({ tone = "warning", children, className = "", role, testId }: Props) {
  const Icon = ICON[tone];
  return (
    <div className={`flex gap-1.5 rounded-lg px-3 py-2 text-sm ${TONE[tone]} ${className}`.trim()} role={role} data-testid={testId}>
      <Icon size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}
