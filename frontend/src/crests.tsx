import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { getCrests } from "./api";

/**
 * Escudos oficiales (URL de la RFFM por nombre de equipo) servidos por `GET /chat/crests`. Con CRESTS_MODE=off (por defecto)
 * el mapa llega vacío y todos los escudos son monogramas. Se pide una vez al arrancar; si falla, también monogramas.
 */
export const crestKey = (name: string): string => name.normalize("NFD").replace(/[̀-ͯ]/g, "").toUpperCase().replace(/\s+/g, " ").trim();

const CrestContext = createContext<ReadonlyMap<string, string>>(new Map());

export function CrestProvider({ children }: { children: ReactNode }) {
  const [crests, setCrests] = useState<ReadonlyMap<string, string>>(() => new Map());
  useEffect(() => {
    let alive = true;
    getCrests()
      .then((r) => {
        const entries = Object.entries(r?.crests ?? {}).filter(([n, u]) => typeof n === "string" && typeof u === "string" && u.startsWith("https://"));
        if (alive && entries.length) setCrests(new Map(entries.map(([n, u]) => [crestKey(n), u])));
      })
      .catch(() => undefined); // sin escudos: monogramas
    return () => { alive = false; };
  }, []);
  return <CrestContext.Provider value={crests}>{children}</CrestContext.Provider>;
}

export const useCrestUrl = (name: string): string | undefined => useContext(CrestContext).get(crestKey(name));
