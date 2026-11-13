/**
 * Escudo de equipo (docs/design-system/04-escudos-equipos.md §4.5). Con CRESTS_MODE=link y escudos en BD, la imagen oficial
 * de la RFFM; si no hay o no carga, el FALLBACK: escudo genérico con las iniciales y un tono derivado del club (sin sufijo de
 * categoría, para que el 'A' y el 'B' coincidan), que nunca imita colores reales. Decorativo: el nombre siempre va al lado.
 */
import { useState } from "react";
import { useCrestUrl } from "../crests";

/** Tonos con texto blanco ≥ 5,7:1 (medido). */
const TONES = ["#A12A20", "#1B5FA6", "#1C7444", "#6B3FA0", "#8A5A12", "#0F6E73", "#4A5563", "#9B2C68"];
/** Palabras que no identifican al equipo: formas jurídicas, «fútbol sala», artículos y letras de categoría. */
const SKIP = new Set(["CD", "CDE", "CF", "AD", "ADC", "CDF", "CDB", "UD", "SD", "AC", "AFS", "CLUB", "FS", "FSALA", "EFS", "EF", "ED", "ESC", "DEP",
  "FUTSAL", "FUTBOL", "SALA", "DEPORTIVO", "DEPORTIVA", "DEPORTIVOS", "ESCUELA", "ASOCIACION", "AGRUPACION",
  "DE", "DEL", "LA", "EL", "LOS", "LAS", "Y", "A", "B", "C", "D", "E", "F"]);

const fold = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase();

/** El club es lo anterior a « - » (lo de después suele ser el patrocinador): «CLUB ARGANDA FUTSAL - ALBERO-MUDANZAS 'A'» → «CLUB ARGANDA FUTSAL». */
const clubPart = (name: string) => fold(name).split(/\s+[-–]\s+/)[0];

/** Club sin sufijo de categoría (`'A'`, `"B"`): base del tono, igual para todos los equipos de un club. */
export function clubKey(name: string): string {
  return clubPart(name).replace(/\s*['"‘’“”]?\b[A-F]\b['"‘’“”]?\s*$/, "").trim();
}

/** Dos iniciales significativas del club: «E.F.S. CIUDAD DE TORREJON - REDPISO» → «CT»; con una sola palabra, sus dos primeras letras: «C.D. DOSA» → «DO». */
export function crestInitials(name: string): string {
  const words = clubPart(name).split(/[\s/]+/).map((w) => w.replace(/[^A-Z0-9]/g, "")).filter((w) => w && !SKIP.has(w));
  if (words.length === 0) return fold(name).match(/[A-Z0-9]/)?.[0] ?? "?";
  return words.length === 1 ? words[0].slice(0, 2) : words.slice(0, 2).map((w) => w[0]).join("");
}

export function crestTone(name: string): string {
  let h = 0;
  for (const c of clubKey(name)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return TONES[h % TONES.length];
}

export default function TeamCrest({ name, size = 24, className = "" }: { name: string; size?: number; className?: string }) {
  const url = useCrestUrl(name);
  const [failed, setFailed] = useState<string | null>(null);
  if (url && failed !== url) {
    // Escudo oficial: enlazado (no copiado) sobre un fondo blanco fijo que iguala JPEG con fondo y PNG transparentes, también en oscuro.
    return (
      <img src={url} alt="" width={size} height={size} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(url)}
        className={`team-crest shrink-0 rounded-[22%] border border-line bg-tile object-contain p-px ${className}`.trim()} data-testid="team-crest" />
    );
  }
  const initials = crestInitials(name);
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false" className={`team-crest shrink-0 ${className}`.trim()} data-testid="team-crest">
      <path d="M12 1.5l8.5 3.2V11c0 5.4-3.6 9.6-8.5 11.5C7.1 20.6 3.5 16.4 3.5 11V4.7z" fill={crestTone(name)} />
      <text x="12" y="14.6" textAnchor="middle" fontFamily='"Archivo Variable", system-ui, sans-serif' fontWeight={750} fontSize={initials.length > 1 ? 8 : 10} fill="#fff"
        style={{ fontStretch: "87.5%" }}>{initials}</text>
    </svg>
  );
}
