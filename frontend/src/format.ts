/** Fecha y hora de un partido en Europe/Madrid (la zona de la RFFM), con independencia de la zona del navegador. */
const MADRID = "Europe/Madrid";

export interface Kickoff {
  /** «sáb 07/11». */
  date: string;
  /** «11:00»; `null` si el dato solo trae la fecha (nunca se inventa una hora). */
  time: string | null;
}

const fmtDay = (timeZone: string) => new Intl.DateTimeFormat("es-ES", { timeZone, weekday: "short", day: "2-digit", month: "2-digit" });
const fmtHour = new Intl.DateTimeFormat("es-ES", { timeZone: MADRID, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

const part = (parts: Intl.DateTimeFormatPart[], type: string) => parts.find((p) => p.type === type)?.value ?? "";
const dayLabel = (parts: Intl.DateTimeFormatPart[]) => `${part(parts, "weekday").replace(/[.,]/g, "")} ${part(parts, "day")}/${part(parts, "month")}`;

/** Mediodía UTC de un día REAL del calendario (rechaza 2026-13-45 en vez de «corregirlo» a otro día). */
function realDay(y: number, m: number, d: number): Date | null {
  const when = new Date(Date.UTC(y, m - 1, d, 12));
  return when.getUTCFullYear() === y && when.getUTCMonth() === m - 1 && when.getUTCDate() === d ? when : null;
}

/**
 * Admite `YYYY-MM-DD` (solo fecha), `YYYY-MM-DDTHH:MM[:SS]` sin zona (se toma como hora local de Madrid) y con zona (se convierte a Madrid).
 * Devuelve `null` si no se puede interpretar (el llamador muestra el texto tal cual).
 */
export function formatKickoff(iso: string | null | undefined): Kickoff | null {
  if (!iso) return null;
  const text = iso.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    const [y, m, d] = text.split("-").map(Number);
    const when = realDay(y, m, d);
    return when ? { date: dayLabel(fmtDay("UTC").formatToParts(when)), time: null } : null;
  }
  const naive = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$/.exec(text);
  if (naive) {
    const [, y, m, d, hh, mm] = naive.map(Number) as unknown as number[];
    const when = realDay(y, m, d);
    if (!when || hh > 23 || mm > 59) return null;
    return { date: dayLabel(fmtDay("UTC").formatToParts(when)), time: `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}` };
  }
  const when = new Date(text);
  if (Number.isNaN(when.getTime())) return null;
  return { date: dayLabel(fmtDay(MADRID).formatToParts(when)), time: fmtHour.format(when) };
}

/** «sáb 07/11 · 11:00», o «sáb 07/11» si no hay hora. */
export function kickoffLabel(iso: string | null | undefined): string {
  const k = formatKickoff(iso);
  if (!k) return iso ? iso : "Sin fecha";
  return k.time ? `${k.date} · ${k.time}` : k.date;
}
