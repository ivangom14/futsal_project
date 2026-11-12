import { useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { applyTheme, loadTheme, type ThemePref } from "../theme";

const OPTIONS: { value: ThemePref; label: string; Icon: typeof Sun }[] = [
  { value: "system", label: "Sistema", Icon: Monitor },
  { value: "light", label: "Claro", Icon: Sun },
  { value: "dark", label: "Oscuro", Icon: Moon },
];

/** Selector de tema (sistema/claro/oscuro). El script de index.html aplica la elección guardada antes del primer pintado. */
export default function ThemeSwitch() {
  const [pref, setPref] = useState<ThemePref>(loadTheme);
  const choose = (p: ThemePref) => {
    applyTheme(p);
    setPref(p);
  };
  return (
    <div role="group" aria-label="Tema" className="grid grid-cols-3 gap-1 rounded-lg bg-surface-2 p-1">
      {OPTIONS.map(({ value, label, Icon }) => (
        <button key={value} type="button" aria-pressed={pref === value} onClick={() => choose(value)}
          className={`flex min-h-10 items-center justify-center gap-1.5 rounded-md text-xs font-medium ${pref === value ? "bg-surface text-ink shadow-sm" : "text-ink-2 hover:text-ink"}`}>
          <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
          {label}
        </button>
      ))}
    </div>
  );
}
