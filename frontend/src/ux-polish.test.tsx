/** Pulido UX: clasificación móvil, goleadores con total, historial con texto visible vacío (el visual es la respuesta). */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import Visuals, { LONG_TEAM } from "./components/Visuals";
import { historyFor } from "./conversations";
import contract from "../../tests/fixtures/chat_response.json";
import type { ChatResponse, Message, Visual } from "./types";

const LONG = "CDB SPORTING LAS AGUILAS DE MADRID - MERCADO LAS AGUILAS 'B'";
const cols = ["position", "team", "played", "won", "drawn", "lost", "goals_for", "goals_against", "goal_difference", "points"]
  .map((key) => ({ key, label: key === "team" ? "Equipo" : key.slice(0, 3).toUpperCase(), align: key === "team" ? "left" : "right" }) as const);
const standings: Visual = { type: "table", title: "Clasificación · PRIMERA · Grupo 2", columns: [...cols], rows: [
  { position: 1, team: LONG, played: 10, won: 8, drawn: 1, lost: 1, goals_for: 40, goals_against: 15, goal_difference: 25, points: 25, highlight: false },
  { position: 1, team: "C.D. DOSA", played: 10, won: 8, drawn: 1, lost: 1, goals_for: 30, goals_against: 15, goal_difference: 15, points: 25, highlight: false }] };

describe("clasificación en móvil", () => {
  it("prioriza Pos, Equipo, PJ y Pts; el resto se pliega por contenedor y conserva orden, empates y datos", () => {
    render(<Visuals visuals={[standings]} />);
    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0].textContent)).toEqual(["1", "1"]); // empate respetado, sin reordenar
    expect(within(table).getByTitle("Diferencia de goles").closest("th")).toHaveClass("st-opt0");
    expect(within(table).getByText("+25")).toBeInTheDocument();
    for (const key of ["position", "team", "played", "points"]) expect(within(table).getAllByRole("columnheader")[cols.findIndex((c) => c.key === key)].className).not.toMatch(/st-opt/);
  });

  it("nombre largo: texto normal con su letra de categoría (sin recuadro), dos líneas y un toque muestra el nombre completo", async () => {
    expect(LONG.length).toBeGreaterThan(LONG_TEAM);
    render(<Visuals visuals={[standings]} />);
    const btn = screen.getByRole("button", { name: `${LONG} (ver nombre completo)` });
    expect(btn).toHaveAttribute("aria-expanded", "false");
    expect(within(btn).getByText(LONG)).toHaveClass("st-name"); // «… 'B'» forma parte del nombre
    expect(document.querySelector(".st-tag")).toBeNull();
    await userEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    expect(btn).toHaveClass("is-open");
    expect(screen.queryByRole("button", { name: /C\.D\. DOSA \(ver nombre completo\)/ })).toBeNull(); // corto: sin botón
  });
});

describe("goleadores", () => {
  it("si la tabla oficial tiene más filas que la tarjeta, lo dice", () => {
    const rows = Array.from({ length: 12 }, (_, i) => ({ label: `Jugador ${i}`, sublabel: "C.D. DOSA", value: 20 - i, rank: i + 1 }));
    render(<Visuals visuals={[{ type: "bars", title: "Goleadores · X", rows, total: 40 }]} />);
    expect(screen.getAllByTestId("rank-row")).toHaveLength(10);
    expect(screen.getByTestId("ranking-expand")).toHaveTextContent("Ver 2 más");
  });
});

describe("historial con respuesta solo visual", () => {
  it("si el texto visible quedó vacío, el historial reenvía el original (el seguimiento conserva los datos)", () => {
    const response = { ...(contract as unknown as ChatResponse), answer: "", answer_original: "El líder es el C.D. DOSA con 25 puntos.", guardrail: null, error: null };
    const msgs: Message[] = [{ id: "u", role: "user", content: "¿Clasificación?", createdAt: 1 }, { id: "a", role: "assistant", content: "", createdAt: 2, response }];
    expect(historyFor(msgs)).toEqual([{ role: "user", content: "¿Clasificación?" }, { role: "assistant", content: "El líder es el C.D. DOSA con 25 puntos." }]);
  });
});
