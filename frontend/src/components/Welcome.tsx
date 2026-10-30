export const EXAMPLES = [
  "¿Cuáles son los próximos partidos de Primera Autonómica Senior Grupo 2?",
  "¿Qué resultados hubo en la última jornada?",
  "¿Quién es el máximo goleador de la competición?",
  "¿Cuándo se aplica la regla de la ventaja en fútbol sala?",
];

export default function Welcome({ onPick, disabled }: { onPick: (q: string) => void; disabled: boolean }) {
  return (
    <div className="mx-auto mt-10 max-w-xl text-center" data-testid="welcome">
      <div className="text-5xl">⚽</div>
      <h2 className="mt-2 text-xl font-semibold">Asistente RFFM de fútbol sala</h2>
      <p className="mt-1 text-sm text-slate-600">
        Pregunta por partidos, jornadas, clasificaciones, goleadores o normativa. Las respuestas salen de los datos oficiales ingeridos y de los documentos indexados.
      </p>
      <p className="mt-1 text-xs text-slate-500">Puedes encadenar preguntas («¿y su equipo?»): el agente recuerda los últimos mensajes de esta conversación, pero vuelve a consultar los datos oficiales cada vez.</p>
      <ul className="mt-5 space-y-2">
        {EXAMPLES.map((q) => (
          <li key={q}>
            <button disabled={disabled} onClick={() => onPick(q)} className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left text-sm hover:border-pitch-500 disabled:opacity-50">
              {q}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
