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
});
