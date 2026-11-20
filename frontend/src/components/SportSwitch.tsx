import Chip from "../ui/Chip";
import { SPORTS, SPORT_LABEL, type Sport } from "../types";

/** Selector segmentado del deporte activo (grupo de dos botones con aria-pressed). Cambiarlo con mensajes abre una conversación nueva (lo gestiona App). */
export default function SportSwitch({ sport, onChange }: { sport: Sport; onChange: (s: Sport) => void }) {
  return (
    <div role="group" aria-label="Deporte" className="flex gap-1.5" data-testid="sport-switch">
      {SPORTS.map((s) => (
        <Chip key={s} aria-pressed={sport === s} variant={sport === s ? "option" : "suggest"} onClick={() => onChange(s)}
          className={`min-w-0 !min-h-9 justify-center px-3 py-1 text-[13px] ${sport === s ? "font-semibold" : "text-ink-2"}`}>
          {SPORT_LABEL[s]}
        </Chip>
      ))}
    </div>
  );
}
