import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import Lineup from "./components/Lineup";
import type { LineupVisual } from "./types";
import contract from "../../tests/fixtures/chat_response.json";

const reply = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
const HEALTH = { status: "ok", agent: { ready: true, error: null }, llm: { provider: "gemini", key_configured: true }, data_api: { reachable: true, detail: null } };
const player = (i: number, o: object = {}) => ({ shirt: i, name: `Jugador Numero ${i}`, short: `J. ${i}`, goalkeeper: false, captain: false, ...o });
const football: LineupVisual = {
  type: "lineup", title: "Alineación", team: "C.D. EJEMPLO", court: false, sport: "football11",
  starters: Array.from({ length: 11 }, (_, i) => player(i + 1, i === 0 ? { goalkeeper: true } : {})),
  substitutes: [player(12), player(13)], unknown: [], note: "Nota de fútbol.",
} as LineupVisual;

describe("Lineup fútbol 11", () => {
  it("lista 11 titulares, sin pista, sin botón y sin aviso de quinteto", () => {
    render(<Lineup v={football} />);
    expect(screen.queryByTestId("lineup-toggle")).toBeNull();
    expect(screen.queryByTestId("lineup-court")).toBeNull();
    expect(screen.queryByText(/quinteto/i)).toBeNull();
    expect(within(screen.getByRole("region", { name: "Titulares" })).getAllByRole("listitem")).toHaveLength(11);
    expect(within(screen.getByRole("region", { name: "Suplentes" })).getAllByRole("listitem")).toHaveLength(2);
  });
  it("incluso con court=true no dibuja la pista", () => {
    render(<Lineup v={{ ...football, court: true }} />);
    expect(screen.queryByTestId("lineup-court")).toBeNull();
    expect(screen.queryByTestId("lineup-toggle")).toBeNull();
  });
  it("fútbol sala sin `sport` sigue con pista y botón", () => {
    const v = { ...football, sport: undefined, court: true, starters: football.starters.slice(0, 5) } as LineupVisual;
    render(<Lineup v={v} />);
    expect(screen.getByTestId("lineup-court")).toBeInTheDocument();
    expect(screen.getByTestId("lineup-toggle")).toBeInTheDocument();
  });
});

describe("sport_switch", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());
  const setup = (withSwitch: boolean) => {
    const m = vi.fn((url: string) => {
      if (url.endsWith("/chat/health")) return reply(HEALTH);
      if (url.includes("/chat/crests")) return reply({ mode: "off", crests: {} });
      return reply({ ...contract, error: null, ...(withSwitch ? { answer: "Cambia a «Fútbol».", guardrail: "otro_deporte", sport_switch: "football11", suggestions: [] } : { guardrail: null }) });
    });
    vi.stubGlobal("fetch", m);
    return m;
  };
  const chats = (m: ReturnType<typeof setup>) =>
    (m.mock.calls as unknown as [string, RequestInit][]).filter(([u]) => String(u).endsWith("/chat")).map(([, i]) => JSON.parse(String(i.body)) as { question: string; sport: string; conversation_id: string });

  it("sin sport_switch no hay botón", async () => {
    setup(false);
    render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "hola{Enter}");
    await waitFor(() => expect(screen.getAllByTestId("msg-agent")).toHaveLength(1));
    expect(screen.queryByRole("button", { name: /Cambiar a/ })).toBeNull();
  });

  it("al pulsarlo abre conversación del otro deporte y repite la misma pregunta", async () => {
    const m = setup(true);
    render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "clasificación del Getafe{Enter}");
    const btn = await screen.findByRole("button", { name: "Cambiar a Fútbol y preguntar" });
    await userEvent.click(btn);
    await waitFor(() => expect(chats(m)).toHaveLength(2));
    const [a, b] = chats(m);
    expect(b).toMatchObject({ question: "clasificación del Getafe", sport: "football11" });
    expect(b.conversation_id).not.toBe(a.conversation_id);
    expect(a.sport).toBe("futsal");
  });
});
