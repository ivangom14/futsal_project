import { LogoFull } from "./Logo";

export const EXAMPLES = [
  "¿Cuáles son los próximos partidos de Primera Autonómica Senior Grupo 2?",
  "¿Qué resultados hubo en la última jornada?",
  "¿Quién es el máximo goleador de la competición?",
  "¿Cuándo se aplica la regla de la ventaja en fútbol sala?",
];

export default function Welcome({ onPick, disabled }: { onPick: (q: string) => void; disabled: boolean }) {
  return (
    <div className="mx-auto mt-6 max-w-xl text-center md:mt-12" data-testid="welcome">
      <div className="flex justify-center"><LogoFull className="h-20 w-auto md:h-24" /></div>
      <h2 className="mt-4 text-xl font-bold text-bear-900">¡Hola! Soy Futsito</h2>
      <p className="mt-1 text-sm text-slate-600">
        Te ayudo con partidos, resultados, clasificaciones, goleadores y normativa del fútbol sala de la RFFM. Respondo con datos oficiales y te enseño de dónde salen.
      </p>
      <p className="mt-1 text-xs text-slate-500">Puedes encadenar preguntas («¿y su equipo?»): recuerdo esta conversación, pero consulto de nuevo los datos oficiales cada vez.</p>
      <ul className="mt-5 space-y-2">
        {EXAMPLES.map((q) => (
          <li key={q}>
            <button disabled={disabled} onClick={() => onPick(q)} className="w-full rounded-xl border border-bear-100 bg-white px-3 py-2 text-left text-sm shadow-sm hover:border-brand-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 disabled:opacity-50">
              {q}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
