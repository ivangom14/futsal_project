import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { STORAGE_KEY } from "./conversations";
import contract from "../../tests/fixtures/chat_response.json";

const reply = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
const HEALTH = { status: "ok", agent: { ready: true, error: null }, llm: { provider: "gemini", key_configured: true }, data_api: { reachable: true, detail: null } };

function backend(focus: Record<string, string> = { team: "EQUIPO A" }) {
  const m = vi.fn((url: string) => {
    if (url.endsWith("/chat/health")) return reply(HEALTH);
    if (url.includes("/chat/crests")) return reply({ mode: "off", crests: {} });
    return reply({ ...contract, guardrail: null, error: null, focus });
  });
  vi.stubGlobal("fetch", m);
  return m;
}
type Body = { question: string; sport: string; focus: Record<string, string>; conversation_id: string };
const chats = (m: ReturnType<typeof backend>) =>
  (m.mock.calls as unknown as [string, RequestInit][]).filter(([u]) => String(u).endsWith("/chat")).map(([, i]) => JSON.parse(String(i.body)) as Body);
const ask = async (q: string) => {
  const before = screen.queryAllByTestId("msg-agent").length;
  await userEvent.type(screen.getByLabelText("Pregunta"), `${q}{Enter}`);
  await waitFor(() => expect(screen.queryAllByTestId("msg-agent").length).toBe(before + 1));
};
const radio = (name: string) => within(screen.getByRole("group", { name: "Deporte" })).getByRole("button", { name });

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("multideporte", () => {
  it("arranca con Fútbol sala seleccionado y pide los escudos de ese deporte", async () => {
    const m = backend();
    render(<App />);
    expect(radio("Fútbol sala")).toHaveAttribute("aria-pressed", "true");
    expect(radio("Fútbol")).toHaveAttribute("aria-pressed", "false");
    await waitFor(() => expect(m.mock.calls.some(([u]) => String(u).endsWith("/chat/crests?sport=futsal"))).toBe(true));
    await userEvent.click(radio("Fútbol"));
    await waitFor(() => expect(m.mock.calls.some(([u]) => String(u).endsWith("/chat/crests?sport=football11"))).toBe(true));
    expect(screen.queryByText(/Primera Autonómica/)).toBeNull(); // sin sugerencias de fútbol sala
  });

  it("cada pregunta envía el deporte; cambiar con mensajes abre una conversación nueva sin reutilizar el foco", async () => {
    const m = backend();
    render(<App />);
    await ask("uno");
    await ask("dos");
    expect(chats(m).map((b) => [b.sport, b.focus])).toEqual([["futsal", {}], ["futsal", { team: "EQUIPO A" }]]);
    await userEvent.click(radio("Fútbol"));
    expect(radio("Fútbol")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("welcome")).toBeInTheDocument(); // conversación nueva y vacía
    await ask("tres");
    const bodies = chats(m);
    expect(bodies[2]).toMatchObject({ sport: "football11", focus: {} });
    expect(bodies[2].conversation_id).not.toBe(bodies[0].conversation_id);
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(stored.conversations.map((c: { sport: string }) => c.sport).sort()).toEqual(["football11", "futsal"]);
  });

  it("abrir una conversación del historial sincroniza el selector", async () => {
    backend();
    const mk = (id: string, sport: string, t: string) => ({ id, sport, title: t, createdAt: 2, updatedAt: 2, messages: [{ id: `${id}m`, role: "user", content: t, createdAt: 2 }] });
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ conversations: [mk("a", "futsal", "Sala"), mk("b", "football11", "Once")], activeId: "a" }));
    render(<App />);
    expect(radio("Fútbol sala")).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Once" }));
    expect(radio("Fútbol")).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Sala" }));
    expect(radio("Fútbol sala")).toHaveAttribute("aria-pressed", "true");
  });

  it("en viewport móvil el selector sigue visible y la app no se rompe", async () => {
    backend();
    const prev = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 360 });
    window.dispatchEvent(new Event("resize"));
    render(<App />);
    expect(screen.getByTestId("sport-switch")).toBeVisible();
    expect(screen.getByLabelText("Pregunta")).toBeInTheDocument();
    Object.defineProperty(window, "innerWidth", { configurable: true, value: prev });
  });
});

describe("bienvenida de fútbol", () => {
  it("muestra sugerencias de Preferente Aficionado con football11", async () => {
    backend();
    render(<App />);
    await userEvent.click(radio("Fútbol"));
    const list = screen.getByLabelText("Consultas de ejemplo");
    expect(within(list).getAllByRole("button")).toHaveLength(4);
    expect(within(list).getByText("Clasificación de Preferente Aficionado, grupo 1")).toBeInTheDocument();
  });
});
