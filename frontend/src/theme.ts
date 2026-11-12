/** Preferencia de tema: «system» sigue a prefers-color-scheme; «light»/«dark» la fuerzan con data-theme en <html> (ver styles/tokens.css). */
export type ThemePref = "system" | "light" | "dark";

/** Misma clave que el script de index.html, que aplica el tema antes del primer pintado. */
export const THEME_KEY = "futsito.theme";

export function loadTheme(): ThemePref {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system"; // almacenamiento bloqueado: se sigue al sistema
  }
}

export function applyTheme(pref: ThemePref): void {
  const root = document.documentElement;
  if (pref === "system") delete root.dataset.theme;
  else root.dataset.theme = pref;
  try {
    if (pref === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, pref);
  } catch {
    /* sin almacenamiento: el tema vale solo para esta visita */
  }
}
