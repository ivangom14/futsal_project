import typography from "@tailwindcss/typography";

/* Colores SEMÁNTICOS del sistema «Pista»: cada uno apunta a una variable de src/styles/tokens.css (claro/oscuro).
   Guía: docs/design-system/03-guia-estilos.md. No usar la paleta por defecto de Tailwind en los componentes. */
const v = (name) => `var(--fs-${name})`;

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: v("canvas"),
        surface: { DEFAULT: v("surface"), 2: v("surface-2") },
        line: { DEFAULT: v("line"), strong: v("line-strong") },
        ink: { DEFAULT: v("ink"), 2: v("ink-2") },
        inverse: { DEFAULT: v("inverse"), on: v("on-inverse") },
        brand: { DEFAULT: v("brand"), hover: v("brand-hover"), text: v("brand-text"), soft: v("brand-soft"), on: v("on-brand") },
        accent: { DEFAULT: v("accent"), soft: v("accent-soft") },
        gold: { DEFAULT: v("gold"), text: v("gold-text") },
        success: { DEFAULT: v("success"), soft: v("success-soft") },
        warning: { DEFAULT: v("warning"), soft: v("warning-soft") },
        danger: { DEFAULT: v("danger"), soft: v("danger-soft") },
        focus: v("focus"),
        chart: { 1: v("chart-1"), 2: v("chart-2") },
        tile: v("tile"),
        scrim: v("scrim"),
      },
      fontFamily: {
        // Marca, títulos y cifras (Archivo, 1 woff2 autoalojado); la conversación usa la pila del sistema (font-sans por defecto).
        display: ['"Archivo Variable"', "system-ui", "sans-serif"],
      },
      typography: {
        DEFAULT: {
          css: {
            "--tw-prose-body": v("ink"),
            "--tw-prose-headings": v("ink"),
            "--tw-prose-lead": v("ink-2"),
            "--tw-prose-links": v("accent"),
            "--tw-prose-bold": v("ink"),
            "--tw-prose-counters": v("ink-2"),
            "--tw-prose-bullets": v("line-strong"),
            "--tw-prose-hr": v("line"),
            "--tw-prose-quotes": v("ink"),
            "--tw-prose-quote-borders": v("line"),
            "--tw-prose-captions": v("ink-2"),
            "--tw-prose-code": v("ink"),
            "--tw-prose-pre-code": v("ink"),
            "--tw-prose-pre-bg": v("surface-2"),
            "--tw-prose-th-borders": v("line-strong"),
            "--tw-prose-td-borders": v("line"),
          },
        },
      },
    },
  },
  plugins: [typography],
};
