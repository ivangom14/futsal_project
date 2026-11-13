/** Fase P1 (segundo bloque) del sistema «Pista»: escudos con monograma, clasificación, goleadores, aclaraciones, supuestos, contexto y deshacer. */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import Visuals from "./components/Visuals";
import { focusLabel } from "./components/Composer";
import { createConversation, restoreConversation, type State } from "./conversations";
import TeamCrest, { clubKey, crestInitials, crestTone } from "./ui/TeamCrest";
import contract from "../../tests/fixtures/chat_response.json";
import type { ChatResponse, Visual } from "./types";

const HEALTH = { status: "ok", agent: { ready: true, error: null }, llm: { provider: "gemini", key_configured: true }, data_api: { reachable: true, detail: null } };
const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
const base = contract as unknown as ChatResponse;

/** Responde a /chat con `answers` en orden (la última se repite) y registra las preguntas enviadas. */
function backend(...answers: unknown[]) {
  const questions: string[] = [];
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (String(url).endsWith("/chat/health")) return json(HEALTH);
    if (String(url).endsWith("/chat")) {
      questions.push(JSON.parse(String(init?.body)).question);
      return json(answers[Math.min(questions.length - 1, answers.length - 1)]);
    }
    return json({ deleted: true });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { questions, fetchMock };
}
const withContext = (context: Record<string, unknown>, over: Partial<ChatResponse> = {}): ChatResponse =>
  ({ ...base, ...over, diagnostics: { ...base.diagnostics, context } }) as ChatResponse;
const ask = async (q: string) => {
  const before = screen.queryAllByTestId("msg-agent").length;
  await userEvent.type(screen.getByLabelText("Pregunta"), `${q}{Enter}`);
  await waitFor(() => expect(screen.queryAllByTestId("msg-agent").length).toBe(before + 1));
};

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("escudo con monograma", () => {
  it.each([
    ["C.D. DOSA", "DO"], ["LOPE DE VEGA FS", "LV"], ["E.F.S. CIUDAD DE TORREJON - REDPISO TORREJON 'B'", "CT"],
    ["CLUB ARGANDA FUTSAL - ALBERO-MUDANZAS SEGOVIANA 'A'", "AR"], ["PARQUE NORTE F.S. - RODILLITO 'B'", "PN"], ["Club Atlético Álamo", "AA"],
  ])("%s → %s", (name, initials) => expect(crestInitials(name)).toBe(initials));

  it("los equipos A y B de un club comparten tono; el patrocinador no cuenta", () => {
    expect(clubKey("CLUB CORAZONISTAS 'A'")).toBe(clubKey("CLUB CORAZONISTAS 'B'"));
    expect(crestTone("E.F.S. CIUDAD DE TORREJON - REDPISO TORREJON 'A'")).toBe(crestTone("E.F.S. CIUDAD DE TORREJON - OTRO 'B'"));
  });

  it("es decorativo (el nombre va al lado)", () => {
    render(<TeamCrest name="C.D. DOSA" />);
    expect(screen.getByTestId("team-crest")).toHaveAttribute("aria-hidden", "true");
  });
});

describe("clasificación", () => {
  const standings: Visual = {
    type: "table", title: "Clasificación · PREFERENTE", columns: [
      { key: "position", label: "Pos", align: "left" }, { key: "team", label: "Equipo", align: "left" }, { key: "played", label: "PJ", align: "right" },
      { key: "won", label: "G", align: "right" }, { key: "goals_for", label: "GF", align: "right" }, { key: "goal_difference", label: "DG", align: "right" },
      { key: "points", label: "Pts", align: "right" }],
    rows: [{ position: 1, team: "C.D. DOSA", played: 5, won: 4, goals_for: 20, goal_difference: 12, points: 13, highlight: true },
      { position: 2, team: "FS ALCALA", played: 5, won: 3, goals_for: 15, goal_difference: -2, points: 10, highlight: false }],
  };

  it("abreviaturas explicadas, escudo junto al equipo, DG con signo y resaltado conservado", () => {
    render(<Visuals visuals={[standings]} />);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Pos", "Equipo", "PJ", "G", "GF", "DG", "Pts"]);
    expect(within(table).getByTitle("Diferencia de goles")).toHaveTextContent("DG");
    expect(within(table).getByText("+12")).toBeInTheDocument();
    expect(within(table).getByText("-2")).toBeInTheDocument();
    const dosa = within(table).getByText("C.D. DOSA").closest("tr")!;
    expect(dosa).toHaveClass("font-semibold");
    expect(within(dosa).getByTestId("team-crest")).toBeInTheDocument();
  });

  it("las columnas plegables siguen en el DOM y el botón las muestra todas", async () => {
    render(<Visuals visuals={[standings]} />);
    const wrap = screen.getByTestId("standings");
    expect(screen.getByText("GF").closest("th")).toHaveClass("st-opt2");
    expect(wrap).not.toHaveAttribute("data-all");
    await userEvent.click(screen.getByRole("button", { name: "Ver todas las columnas" }));
    expect(wrap).toHaveAttribute("data-all");
    expect(screen.getByRole("button", { name: "Ver menos columnas" })).toHaveAttribute("aria-pressed", "true");
  });

  it("una tabla que no es clasificación se pinta como antes", () => {
    render(<Visuals visuals={[{ type: "table", title: "Otra", columns: [{ key: "x", label: "X", align: "left" }], rows: [{ x: "a" }] }]} />);
    expect(screen.queryByTestId("standings")).not.toBeInTheDocument();
  });
});

describe("goleadores", () => {
  it("rango en oro para el primero y escudo del equipo; la barra conserva su nombre accesible", () => {
    render(<Visuals visuals={[{ type: "bars", title: "Goleadores", rows: [
      { label: "Juan Pérez", sublabel: "C.D. DOSA", value: 9, rank: 1 }, { label: "Leo Martín", sublabel: "FS ALCALA", value: 5, rank: 2 }] }]} />);
    expect(screen.getByText("1")).toHaveClass("bg-gold");
    expect(screen.getByText("2")).not.toHaveClass("bg-gold");
    expect(screen.getAllByTestId("team-crest")).toHaveLength(2);
    expect(screen.getByRole("img", { name: "Juan Pérez: 9" })).toBeInTheDocument();
  });
});

describe("aclaraciones y supuestos", () => {
  it("una aclaración ofrece las opciones pulsables (en lugar de las sugeridas) y al pulsar se envía la opción", async () => {
    const clar = withContext({ status: "ask", options: ["PREFERENTE FS", "PRIMERA AUTONÓMICA FS"] },
      { answer: "¿A cuál de estas competiciones te refieres: «PREFERENTE FS»; «PRIMERA AUTONÓMICA FS», o a otra?", suggestions: ["Sugerida que no debe salir"] });
    const { questions } = backend(clar, base);
    render(<App />);
    await ask("¿Cómo va la liga?");
    const agent = screen.getByTestId("msg-agent");
    expect(agent).toHaveAccessibleName("Aclaración de Futsito");
    expect(within(agent).getByText("Necesito un detalle")).toBeInTheDocument();
    expect(within(agent).queryByText("Sugerida que no debe salir")).not.toBeInTheDocument();
    await userEvent.click(within(within(agent).getByTestId("clarification-options")).getByRole("button", { name: "PREFERENTE FS" }));
    await waitFor(() => expect(questions).toEqual(["¿Cómo va la liga?", "PREFERENTE FS"]));
  });

  it("el supuesto antepuesto por el backend se muestra como nota y no se repite en el texto", async () => {
    const assumption = "Entiendo que hablas de PREFERENTE FS · Grupo 1.";
    backend(withContext({ status: "assume", assumption, assumption_prepended: true }, { answer: `${assumption} El líder es el DOSA.` }));
    render(<App />);
    await ask("¿Quién va primero?");
    const agent = screen.getByTestId("msg-agent");
    expect(within(agent).getByTestId("assumption")).toHaveTextContent(assumption);
    expect(within(agent).getByText("El líder es el DOSA.")).toBeInTheDocument();
    expect(agent.querySelector(".prose")).not.toHaveTextContent(/Entiendo que hablas de/); // el texto ya no lo repite
  });

  it("si el texto no empieza exactamente por el supuesto, no se separa nada", async () => {
    backend(withContext({ status: "assume", assumption: "Entiendo que hablas de X.", assumption_prepended: false }, { answer: "Respuesta normal." }));
    render(<App />);
    await ask("hola");
    expect(screen.queryByTestId("assumption")).not.toBeInTheDocument();
  });
});

describe("chip de contexto", () => {
  it("muestra el foco y la × empieza una conversación nueva sin contexto", async () => {
    backend({ ...base, focus: { team: "C.D. DOSA", competition: "PREFERENTE FS" } });
    render(<App />);
    await ask("¿Cuándo juega el DOSA?");
    const hint = screen.getByTestId("context-hint");
    expect(hint).toHaveTextContent("Hablando de C.D. DOSA");
    await userEvent.click(within(hint).getByRole("button", { name: /Quitar el contexto/ }));
    expect(screen.queryByTestId("context-hint")).not.toBeInTheDocument();
    expect(screen.getByTestId("welcome")).toBeInTheDocument();
  });

  it("etiqueta: equipo o jugador; si no, competición y grupo", () => {
    expect(focusLabel({ competition: "PREFERENTE FS", group: "Grupo 2" })).toBe("PREFERENTE FS · Grupo 2");
    expect(focusLabel({ player: "Juan Pérez", competition: "X" })).toBe("Juan Pérez");
    expect(focusLabel({})).toBeNull();
  });
});

describe("deshacer al borrar", () => {
  it("restaura la conversación con sus mensajes y la activa; el servidor ya recibió el borrado", async () => {
    const { fetchMock } = backend(base);
    render(<App />);
    await ask("Para recuperar");
    await userEvent.click(screen.getByRole("button", { name: /Borrar conversación Para recuperar/ }));
    expect(screen.queryByRole("button", { name: "Para recuperar" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([u, i]) => String(u).includes("/chat/conversations/") && (i as RequestInit)?.method === "DELETE")).toBe(true);
    const toast = screen.getByTestId("undo-toast");
    expect(toast).toHaveTextContent("Conversación «Para recuperar» borrada");
    await userEvent.click(within(toast).getByRole("button", { name: "Deshacer" }));
    expect(screen.getByRole("button", { name: "Para recuperar" })).toHaveAttribute("aria-current", "true");
    expect(screen.getByTestId("msg-agent")).toBeInTheDocument();
    expect(screen.queryByTestId("undo-toast")).not.toBeInTheDocument();
  });

  it("el aviso desaparece solo", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    backend(base);
    render(<App />);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await user.type(screen.getByLabelText("Pregunta"), "Temporal{Enter}");
    await screen.findByTestId("msg-agent");
    await user.click(screen.getByRole("button", { name: /Borrar conversación Temporal/ }));
    expect(screen.getByTestId("undo-toast")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(6_100); });
    expect(screen.queryByTestId("undo-toast")).not.toBeInTheDocument();
  });

  it("restoreConversation: vuelve a su sitio y descarta la vacía creada al borrar", () => {
    const a = { ...createConversation(), title: "A", messages: [{ id: "m", role: "user" as const, content: "x", createdAt: 1 }] };
    const b = { ...createConversation(), title: "B", messages: [{ id: "n", role: "user" as const, content: "y", createdAt: 2 }] };
    const empty = createConversation();
    const after: State = { conversations: [empty, b], activeId: empty.id };
    const restored = restoreConversation(after, a, 0);
    expect(restored.conversations.map((c) => c.title)).toEqual(["A", "B"]);
    expect(restored.activeId).toBe(a.id);
  });
});
