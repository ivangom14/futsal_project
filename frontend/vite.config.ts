/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { fs: { allow: [".."] } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    // Las pruebas ven los detalles técnicos (en la app están ocultos salvo VITE_SHOW_DIAGNOSTICS=1 o ?debug).
    env: { VITE_SHOW_DIAGNOSTICS: "1" },
  },
});
