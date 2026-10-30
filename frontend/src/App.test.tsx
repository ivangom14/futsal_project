import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import contract from "../../tests/fixtures/chat_response.json";
import type { ChatResponse, Health } from "./types";

const HEALTH_OK: Health = {
  status: "ok",
  agent: { ready: true, error: null },
  llm: { provider: "gemini", key_configured: true },
  data_api: { reachable: true, detail: null },
};

type Handler = (body: { question: string }) => Promise<Response>;
const reply = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));

function backend(chat: Handler, health: () => Promise<Response> = () => reply(HEALTH_OK)) {
  const fetchMock = vi.fn((url: string, init?: RequestInit) =>
    url.endsWith("/chat/health") ? health() : chat(JSON.parse(String(init?.body))),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const chatCalls = (m: ReturnType<typeof backend>) => m.mock.calls.filter(([u]) => String(u).endsWith("/chat"));
const sentBodies = (m: ReturnType<typeof backend>) =>
  chatCalls(m).map(([, init]) => JSON.parse(String((init as RequestInit).body)) as { question: string; conversation_id: string; history: { role: string; content: string }[] });
const answers = () => screen.queryAllByTestId("msg-agent").length + screen.queryAllByTestId("msg-error").length;
const ask = async (q: string) => {
  const before = answers();
  await userEvent.type(screen.getByLabelText("Pregunta"), `${q}{Enter}`);
  await waitFor(() => expect(answers()).toBe(before + 1));
};

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("muestra bienvenida con ejemplos y estado de conexión", async () => {
    backend(() => reply(contract));
    render(<App />);
    expect(screen.getByTestId("welcome")).toBeInTheDocument();
    expect(screen.getByText(/próximos partidos de Primera Autonómica/)).toBeInTheDocument();
    expect(await screen.findByText("Conectado")).toBeInTheDocument();
  });

  it("envía con Enter, muestra carga, Markdown, fuentes y detalles técnicos del contrato real", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const m = backend(async () => { await gate; return reply({ ...contract, answer: "Cada mitad dura **20 minutos**." }); });
    render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "¿Cuánto dura cada mitad?{Enter}");
    expect(await screen.findByRole("status")).toHaveTextContent(/consultando/i);
    expect(screen.getByRole("button", { name: "Enviar" })).toBeDisabled();
    release();
    const agent = await screen.findByTestId("msg-agent");
    expect(within(agent).getByText("20 minutos").tagName).toBe("STRONG");
    expect(chatCalls(m)).toHaveLength(1);
    expect(screen.getByText(/Fuentes consultadas \(2\)/)).toBeInTheDocument();
    expect(screen.getByText("Detalles técnicos")).toBeInTheDocument();
    expect(screen.getByText(/search_rules/)).toBeInTheDocument();
    expect(screen.queryByTestId("welcome")).not.toBeInTheDocument();
  });

  it("no muestra fuentes si el agente no devuelve ninguna", async () => {
    backend(() => reply({ ...contract, sources: [] }));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "hola{Enter}");
    await screen.findByTestId("msg-agent");
    expect(screen.queryByText(/Fuentes consultadas/)).not.toBeInTheDocument();
  });

  it("distingue un error del backend de una respuesta y permite reintentar", async () => {
    let fail = true;
    const m = backend(() => (fail ? reply({ detail: "error del agente: cuota agotada" }, 502) : reply(contract)));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "¿Quién lidera?{Enter}");
    const err = await screen.findByTestId("msg-error");
    expect(err).toHaveTextContent("cuota agotada");
    expect(screen.queryByTestId("msg-agent")).not.toBeInTheDocument();
    fail = false;
    await userEvent.click(within(err).getByRole("button", { name: "Reintentar" }));
    await screen.findByTestId("msg-agent");
    expect(screen.queryByTestId("msg-error")).not.toBeInTheDocument();
    const bodies = chatCalls(m).map(([, init]) => JSON.parse(String((init as RequestInit).body)).question);
    expect(bodies).toEqual(["¿Quién lidera?", "¿Quién lidera?"]);
    expect(screen.getAllByTestId("msg-user")).toHaveLength(1);
  });

  it("avisa de una ejecución incompleta sin tratarla como error total", async () => {
    const partial: ChatResponse = { ...contract, error: "límite de 5 tool calls alcanzado", guardrail: "limite_herramientas" } as ChatResponse;
    backend(() => reply(partial));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "x{Enter}");
    expect(await screen.findByText(/Ejecución incompleta/)).toBeInTheDocument();
    expect(screen.queryByTestId("msg-error")).not.toBeInTheDocument();
  });

  it("gestiona conversaciones independientes: nueva, cambiar y borrar", async () => {
    backend(() => reply(contract));
    render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "Primera pregunta{Enter}");
    await screen.findByTestId("msg-agent");
    await userEvent.click(screen.getByRole("button", { name: "+ Nueva conversación" }));
    expect(screen.getByTestId("welcome")).toBeInTheDocument();
    expect(screen.queryByTestId("msg-agent")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Primera pregunta" }));
    expect(screen.getByTestId("msg-agent")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Borrar conversación Primera pregunta/ }));
    expect(screen.getByTestId("welcome")).toBeInTheDocument();
    expect(screen.queryByText("Primera pregunta")).not.toBeInTheDocument();
  });

  it("restaura el historial guardado en el navegador", async () => {
    backend(() => reply(contract));
    const first = render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "Persistente{Enter}");
    await screen.findByTestId("msg-agent");
    first.unmount();
    render(<App />);
    expect(screen.getByRole("button", { name: "Persistente" })).toBeInTheDocument();
    expect(screen.getByTestId("msg-agent")).toBeInTheDocument();
  });

  it("refleja backend caído y servicio degradado", async () => {
    backend(() => reply(contract), () => Promise.reject(new TypeError("down")));
    const a = render(<App />);
    expect(await screen.findByText("Sin conexión")).toBeInTheDocument();
    a.unmount();
    const degraded: Health = { ...HEALTH_OK, status: "degraded", llm: { provider: "gemini", key_configured: false } };
    backend(() => reply(contract), () => reply(degraded));
    render(<App />);
    const badge = await screen.findByRole("button", { name: /Servicio degradado.*falta la clave/ });
    await waitFor(() => expect(badge).toBeInTheDocument());
  });

  it("envía el contexto de la conversación y el mismo conversation_id en las preguntas encadenadas", async () => {
    const m = backend(() => reply({ ...contract, answer: "El máximo goleador es Juan Pérez." }));
    render(<App />);
    await ask("¿Quién es el máximo goleador?");
    await ask("¿En qué equipo juega?");
    const [first, second] = sentBodies(m);
    expect(first.history).toEqual([]);
    expect(second.history).toEqual([
      { role: "user", content: "¿Quién es el máximo goleador?" },
      { role: "assistant", content: "El máximo goleador es Juan Pérez." },
    ]);
    expect(second.conversation_id).toBe(first.conversation_id);
    expect(await screen.findByTestId("context-hint")).toBeInTheDocument();
  });

  it("aísla las conversaciones y una nueva empieza sin contexto (casos C y D)", async () => {
    const m = backend(() => reply(contract));
    render(<App />);
    await ask("Pregunta sobre el equipo A");
    await userEvent.click(screen.getByRole("button", { name: "+ Nueva conversación" }));
    expect(screen.queryByTestId("context-hint")).not.toBeInTheDocument();
    await ask("¿Y su próximo partido?");
    await userEvent.click(screen.getByRole("button", { name: "Pregunta sobre el equipo A" }));
    await ask("Otra del equipo A");
    const [a1, b1, a2] = sentBodies(m);
    expect(b1.history).toEqual([]);
    expect(b1.conversation_id).not.toBe(a1.conversation_id);
    expect(a2.conversation_id).toBe(a1.conversation_id);
    expect(a2.history.map((h) => h.content)).toEqual(["Pregunta sobre el equipo A", contract.answer]);
    expect(JSON.stringify(a2.history)).not.toContain("próximo partido");
  });

  it("no incluye en el contexto una pregunta fallida ni su reintento duplicado", async () => {
    let fail = true;
    const m = backend(() => (fail ? reply({ detail: "error del agente" }, 502) : reply(contract)));
    render(<App />);
    await ask("Primera");
    fail = false;
    await userEvent.click(within(await screen.findByTestId("msg-error")).getByRole("button", { name: "Reintentar" }));
    await screen.findByTestId("msg-agent");
    await ask("Segunda");
    const bodies = sentBodies(m);
    expect(bodies[1].history).toEqual([]); // el reintento no arrastra la pregunta fallida
    expect(bodies[2].history.map((h) => h.content)).toEqual(["Primera", contract.answer]);
  });

  it("pide al servidor olvidar la conversación al borrarla", async () => {
    const m = backend(() => reply(contract));
    render(<App />);
    await ask("Para borrar");
    await userEvent.click(screen.getByRole("button", { name: /Borrar conversación Para borrar/ }));
    await waitFor(() => expect(m.mock.calls.some(([u, i]) => String(u).includes("/chat/conversations/") && (i as RequestInit)?.method === "DELETE")).toBe(true));
  });

  it("tras recargar, continúa la conversación con su contexto (caso E)", async () => {
    const m = backend(() => reply(contract));
    const first = render(<App />);
    await ask("Primera pregunta");
    await ask("Segunda pregunta");
    first.unmount();
    render(<App />);
    await ask("Tercera pregunta");
    const bodies = sentBodies(m);
    expect(bodies[2].conversation_id).toBe(bodies[0].conversation_id);
    expect(bodies[2].history.map((h) => h.content)).toEqual(["Primera pregunta", contract.answer, "Segunda pregunta", contract.answer]);
  });
});
