import { BookOpen, CalendarDays, ListOrdered, Medal, type LucideIcon } from "lucide-react";
import { LogoMark } from "./Logo";
import type { Sport } from "../types";

/**
 * Sugerencias del estado inicial: una por capacidad VERIFICADA del agente (partidos de un día/fin de semana por categoría, tabla
 * oficial, goleadores, reglamento con RAG). Son consultas reales, sin datos inventados; si falta un detalle, Futsito lo pregunta.
 */
export const EXAMPLES: { q: string; Icon: LucideIcon }[] = [
  { q: "¿Qué partidos de sénior hay este fin de semana?", Icon: CalendarDays },
  { q: "Clasificación de Primera Autonómica sénior, grupo 2", Icon: ListOrdered },
  { q: "Máximos goleadores de Primera Autonómica sénior, grupo 2", Icon: Medal },
  { q: "¿Cuánto dura un partido de fútbol sala?", Icon: BookOpen },
];

/** Estado inicial: qué hace Futsito en una línea y cuatro consultas de un toque, pegadas al compositor (zona del pulgar). */
export default function Welcome({ onPick, disabled, sport = "futsal" }: { onPick: (q: string) => void; disabled: boolean; sport?: Sport }) {
  const futsal = sport === "futsal"; // las sugerencias son de fútbol sala: con otro deporte no se muestran
  return (
    <div className="flex flex-1 flex-col justify-end pb-3 pt-8 md:justify-center md:pb-10" data-testid="welcome">
      <div className="flex items-center gap-3">
        <LogoMark className="h-12 w-12 shrink-0" />
        <div className="min-w-0">
          <h2 className="font-display text-xl font-extrabold tracking-tight text-ink md:text-2xl">¿Qué quieres saber?</h2>
          <p className="text-[15px] leading-snug text-ink-2">
            Partidos, resultados, clasificaciones, goleadores, actas y reglamento {futsal ? "del fútbol sala" : "del fútbol"} de la RFFM, con datos oficiales.
          </p>
        </div>
      </div>
      {futsal && <ul className="mt-5 grid gap-2 sm:grid-cols-2" aria-label="Consultas de ejemplo">
        {EXAMPLES.map(({ q, Icon }) => (
          <li key={q}>
            <button disabled={disabled} onClick={() => onPick(q)}
              className="flex min-h-touch w-full items-center gap-3 rounded-md border border-line bg-surface px-3 py-2.5 text-left text-[15px] leading-snug text-ink transition-colors hover:border-line-strong hover:bg-surface-2 disabled:opacity-50">
              <Icon size={18} strokeWidth={1.75} aria-hidden="true" className="shrink-0 text-brand-text" />
              <span className="min-w-0">{q}</span>
            </button>
          </li>
        ))}
      </ul>}
      <p className="mt-3 text-[13px] text-ink-2">Puedes encadenar preguntas: «¿y sus próximos partidos?».</p>
    </div>
  );
}
