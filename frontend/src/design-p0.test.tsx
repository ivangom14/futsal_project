/** Fase P0 del sistema de diseño «Pista» (docs/design-system/): estados traducidos, cajón accesible, tema e iconos sin emoji. */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import Matches from "./components/Matches";
import ThemeSwitch from "./components/ThemeSwitch";
import contract from "../../tests/fixtures/chat_response.json";
import { matchStatus } from "./format";
import { THEME_KEY, applyTheme, loadTheme } from "./theme";
import type { MatchesVisual } from "./types";

const HEALTH = { status: "ok", agent: { ready: true, error: null }, llm: { provider: "gemini", key_configured: true }, data_api: { reachable: true, detail: null } };
const reply = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
const FUNCTIONAL_EMOJI = /[☰✕🗑👍👎⚽🧤🟨🟥]/u;

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  vi.stubGlobal("fetch", vi.fn((url: string) => reply(String(url).endsWith("/chat/health") ? HEALTH : contract)));
});
afterEach(() => vi.unstubAllGlobals());

describe("estado de los partidos", () => {
  it.each([
    ["finished", "Finalizado"], ["scheduled", "Programado"], ["postponed", "Aplazado"],
    ["suspended", "Suspendido"], ["cancelled", "Cancelado"], ["unknown", "Sin confirmar"], ["", "Sin confirmar"], ["POSTPONED", "Aplazado"],
  ])("%s → %s", (raw, label) => expect(matchStatus(raw).label).toBe(label));

  it("la tarjeta nunca muestra el valor en inglés y conserva el original en title", () => {
    const row = (status: string) => ({ round: 3, date: "2026-11-08", home: "A", away: "B", score: null, status, past_due: false });
    const v: MatchesVisual = { type: "matches", title: "Partidos", total: 4, partial: false, rows: ["postponed", "suspended", "cancelled", "raro"].map(row) };
    render(<Matches v={v} />);
    const chips = screen.getAllByTestId("match-status").map((c) => c.textContent);
    expect(chips).toEqual(["Aplazado", "Suspendido", "Cancelado", "Sin confirmar"]);
    expect(screen.queryByText(/postponed|suspended|cancelled/)).not.toBeInTheDocument();
    expect(screen.getByText("Sin confirmar")).toHaveAttribute("title", "Estado en la fuente: raro");
  });
});

describe("menú móvil accesible", () => {
  it("se abre como diálogo modal, atrapa el foco, se cierra con Escape y devuelve el foco", async () => {
    render(<App />);
    const opener = screen.getByRole("button", { name: "Abrir conversaciones" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Conversaciones" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(opener).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "+ Nueva conversación" })).toHaveFocus();
    expect(document.querySelector("main")).toHaveAttribute("inert");

    await userEvent.tab({ shift: true }); // desde el primero, vuelve al último del panel (no sale al chat)
    expect(dialog.contains(document.activeElement)).toBe(true);
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "+ Nueva conversación" })).toHaveFocus();

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.querySelector("main")).not.toHaveAttribute("inert");
    expect(opener).toHaveFocus();
  });

  it("el fondo y el botón de cerrar también cierran el cajón", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Abrir conversaciones" }));
    await userEvent.click(screen.getByTestId("drawer-backdrop"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Abrir conversaciones" }));
    await userEvent.click(screen.getByRole("button", { name: "Cerrar menú" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("tema claro/oscuro", () => {
  it("por defecto sigue al sistema; la elección se aplica en <html> y se recuerda", async () => {
    expect(loadTheme()).toBe("system");
    render(<ThemeSwitch />);
    expect(screen.getByRole("button", { name: "Sistema" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Oscuro" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(screen.getByRole("button", { name: "Oscuro" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Sistema" }));
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
  });

  it("un valor guardado no válido o un almacenamiento bloqueado no rompen nada", () => {
    localStorage.setItem(THEME_KEY, "neon");
    expect(loadTheme()).toBe("system");
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("bloqueado"); });
    expect(() => applyTheme("light")).not.toThrow();
    expect(document.documentElement.dataset.theme).toBe("light");
    spy.mockRestore();
  });
});

describe("iconos", () => {
  it("la interfaz no usa emojis funcionales y los botones de icono conservan su nombre accesible", async () => {
    render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "¿Cuándo se aplica la ventaja?{Enter}");
    const agent = await screen.findByTestId("msg-agent");
    await waitFor(() => expect(within(agent).getByRole("button", { name: "Respuesta útil" })).toBeInTheDocument());
    for (const name of ["Respuesta útil", "Respuesta mejorable"]) {
      expect(within(agent).getByRole("button", { name }).querySelector("svg")).not.toBeNull();
    }
    expect(screen.getByRole("button", { name: "Abrir conversaciones" }).querySelector("svg")).not.toBeNull();
    expect(screen.getByRole("button", { name: /Borrar conversación/ }).querySelector("svg")).not.toBeNull();
    expect(document.body.textContent ?? "").not.toMatch(FUNCTIONAL_EMOJI);
  });
});
