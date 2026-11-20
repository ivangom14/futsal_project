import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { API_URL, getCrests } from "./api";
import type { Sport } from "./types";

/**
 * Escudos oficiales por nombre de equipo, de `GET /chat/crests`: copias propias (CRESTS_MODE=local) o imágenes de la RFFM
 * (CRESTS_MODE=link). Con CRESTS_MODE=off (por defecto) el mapa llega vacío y todos los escudos son monogramas. Se pide una vez al arrancar; si falla, también monogramas.
 */
export const crestKey = (name: string): string => name.normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase().replace(/\s+/g, " ").trim();

/** Solo dos orígenes válidos: la RFFM por HTTPS (CRESTS_MODE=link) o la copia propia del servicio de chat (CRESTS_MODE=local). */
export function resolveCrestUrl(u: unknown): string | null {
  if (typeof u !== "string") return null;
  if (u.startsWith("https://")) return u;
  if (/^\/chat\/crests\/img\/[0-9a-f]{20}\.webp$/.test(u)) return `${API_URL}${u}`;
  return null;
}

const CrestContext = createContext<ReadonlyMap<string, string>>(new Map());

const NO_CRESTS: ReadonlyMap<string, string> = new Map();

/** Los escudos se piden con el deporte activo y se cachean por deporte (al volver a un deporte ya cargado no se repite la petición). */
export function CrestProvider({ sport, children }: { sport: Sport; children: ReactNode }) {
  const [bySport, setBySport] = useState<Partial<Record<Sport, ReadonlyMap<string, string>>>>({});
  const cached = bySport[sport] !== undefined;
  useEffect(() => {
    if (cached) return;
    let alive = true;
    getCrests(sport)
      .then((r) => {
        const entries = Object.entries(r?.crests ?? {})
          .map(([n, u]) => [n, resolveCrestUrl(u)] as const)
          .filter((e): e is readonly [string, string] => typeof e[0] === "string" && e[1] !== null);
        if (alive && entries.length) setBySport((m) => ({ ...m, [sport]: new Map(entries.map(([n, u]) => [crestKey(n), u])) }));
      })
      .catch(() => undefined); // sin escudos: monogramas
    return () => { alive = false; };
  }, [sport, cached]);
  return <CrestContext.Provider value={bySport[sport] ?? NO_CRESTS}>{children}</CrestContext.Provider>;
}

export const useCrestUrl = (name: string): string | undefined => useContext(CrestContext).get(crestKey(name));
