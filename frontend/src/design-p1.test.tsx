/** Fase P1 (primer bloque) del sistema «Pista»: turnos del chat, loader, caja de entrada, diagnóstico oculto y pie de fuentes. */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import Composer, { MAX_CHARS } from "./components/Composer";
import SourceFooter, { groupSources } from "./components/SourceFooter";
import contract from "../../tests/fixtures/chat_response.json";
import visuals from "../../tests/fixtures/chat_response_visuals.json";
import type { Source } from "./types";

const HEALTH = { status: "ok", agent: { ready: true, error: null }, llm: { provider: "gemini", key_configured: true }, data_api: { reachable: true, detail: null }, context_mode: "deterministic" };
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

/** /chat queda pendiente hasta llamar a `release` (o se rechaza si se aborta), para ver el estado de carga. */
function pendingBackend(body: unknown = contract) {
  let release = () => {};
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    if (String(url).endsWith("/chat/health")) return Promise.resolve(json(HEALTH));
    return new Promise<Response>((resolve, reject) => {
      release = () => resolve(json(body));
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    });
  }));
  return () => release();
}
const immediateBackend = (body: unknown = contract) =>
  vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(json(String(url).endsWith("/chat/health") ? HEALTH : body))));

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

describe("turnos del chat", () => {
  it("la respuesta va sin burbuja, con cabecera «Futsito» y procedencia; la pregunta en burbuja inversa", async () => {
    immediateBackend();
    render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "¿Cuánto dura cada mitad?{Enter}");
    const agent = await screen.findByTestId("msg-agent");
    expect(agent.tagName).toBe("ARTICLE");
    expect(agent).toHaveAccessibleName("Respuesta de Futsito");
    expect(within(agent).getByText("Futsito")).toBeInTheDocument();
    expect(within(agent).getByText("Normativa y documentos")).toBeInTheDocument();
    expect(agent.className).not.toMatch(/\bborder\b|max-w-\[/); // a todo el ancho, sin marco
    expect(within(screen.getByTestId("msg-user")).getByText("¿Cuánto dura cada mitad?").className).toMatch(/\bbg-inverse\b/);
  });
});

describe("carga y cancelación", () => {
  it("muestra el loader con texto de estado, cambia Enviar por Detener y al detener queda «Consulta cancelada» en tono neutro", async () => {
    pendingBackend();
    render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "Clasificación{Enter}");
    const status = await screen.findByTestId("agent-status");
    expect(status.querySelector("svg.futsito-loader")).toHaveAttribute("aria-hidden", "true");
    expect(within(status).getByRole("status")).toHaveTextContent("Futsito está consultando los datos oficiales…");
    expect(within(status).getByRole("button", { name: "Cancelar" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Detener" }));
    const err = await screen.findByTestId("msg-error");
    expect(within(err).getByText("Consulta cancelada")).toBeInTheDocument();
    expect(err.className).not.toMatch(/\bbg-danger-soft\b/);
    expect(screen.queryByTestId("agent-status")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enviar" })).toBeDisabled(); // campo vacío
  });

  it("enseña el tiempo transcurrido a partir de 5 s, fuera del texto que se anuncia", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const release = pendingBackend();
    render(<App />);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await user.type(screen.getByLabelText("Pregunta"), "Partidos{Enter}");
    const status = await screen.findByTestId("agent-status");
    expect(status).not.toHaveTextContent(/\d+ s/);
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
    const elapsed = within(status).getByTestId("elapsed");
    expect(elapsed).toHaveTextContent(/· \d+ s/);
    expect(elapsed).toHaveAttribute("aria-hidden", "true");
    await act(async () => release());
    await waitFor(() => expect(screen.queryByTestId("agent-status")).not.toBeInTheDocument());
  });

  it("con la consulta en otra conversación no se ofrece Detener aquí: Enviar queda deshabilitado", async () => {
    pendingBackend();
    render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "Primera{Enter}");
    await screen.findByTestId("agent-status");
    await userEvent.click(screen.getByRole("button", { name: "+ Nueva conversación" }));
    expect(screen.queryByRole("button", { name: "Detener" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enviar" })).toBeDisabled();
  });
});

describe("caja de entrada", () => {
  it("cuenta caracteres solo cerca del límite", async () => {
    render(<Composer onSend={() => {}} loading={false} />);
    const box = screen.getByLabelText("Pregunta");
    await userEvent.type(box, "hola");
    expect(screen.queryByTestId("char-count")).not.toBeInTheDocument();
    await userEvent.click(box);
    await userEvent.paste("x".repeat(MAX_CHARS - 150));
    expect(screen.getByTestId("char-count")).toHaveTextContent(`${MAX_CHARS - 146}/${MAX_CHARS}`);
  });
});

describe("diagnóstico", () => {
  it("sin VITE_SHOW_DIAGNOSTICS ni ?debug, no hay detalles técnicos ni modo de contexto", async () => {
    vi.stubEnv("VITE_SHOW_DIAGNOSTICS", "0");
    immediateBackend();
    render(<App />);
    expect(await screen.findByText("Conectado")).toBeInTheDocument();
    expect(screen.queryByTestId("context-mode")).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Pregunta"), "¿Cuánto dura cada mitad?{Enter}");
    const agent = await screen.findByTestId("msg-agent");
    expect(within(agent).queryByText("Detalles técnicos")).not.toBeInTheDocument();
  });

  it("se puede activar con localStorage futsito.debug", async () => {
    vi.stubEnv("VITE_SHOW_DIAGNOSTICS", "0");
    localStorage.setItem("futsito.debug", "1");
    immediateBackend();
    render(<App />);
    expect(await screen.findByTestId("context-mode")).toHaveTextContent("deterministic");
  });
});

describe("pie de fuentes", () => {
  const src = (over: Partial<Source>): Source => ({
    kind: "official_data", title: "Clasificación oficial · RFFM", detail: "PREFERENTE · Grupo 1", pages: null, text: null, cited: null,
    queried_at: null, updated_at: null, freshness: null, updated_note: null, meta: {}, ...over,
  });

  it("agrupa duplicados, junta páginas y omite fragmentos no citados", () => {
    const g = groupSources([
      src({ kind: "document", title: "Reglas", detail: "Regla 7", pages: "31", cited: true }),
      src({ kind: "document", title: "Reglas", detail: "Regla 7", pages: "32", cited: true }),
      src({ kind: "document", title: "Circular 10", detail: null, pages: "2", cited: false }),
    ]);
    expect(g).toHaveLength(1);
    expect(g[0].pageList).toEqual(["31", "32"]);
  });

  it("datos oficiales: fecha de actualización si se conoce, aviso si no se conoce o están desactualizados", async () => {
    render(<SourceFooter sources={[
      src({ updated_at: "2026-10-28T09:00:00+00:00", freshness: "known" }),
      src({ title: "Partidos y resultados · RFFM", freshness: "unknown" }),
      src({ title: "Goleadores oficiales · RFFM", updated_at: "2026-09-01T09:00:00+00:00", freshness: "stale" }),
    ]} />);
    expect(screen.getByText(/Fuente: Clasificación oficial · RFFM y 2 más/)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/Fuente:/));
    expect(screen.getByText(/actualizado 28\/10\/2026/)).toBeInTheDocument();
    expect(screen.getByText(/fecha de actualización no disponible/)).toBeInTheDocument();
    expect(screen.getByText(/puede estar desactualizado/)).toBeInTheDocument();
  });

  it("sin fuentes no pinta nada", () => {
    const { container } = render(<SourceFooter sources={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("respuesta real con datos oficiales: el pie va tras los visuales", async () => {
    immediateBackend(visuals);
    render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "Clasificación{Enter}");
    const agent = await screen.findByTestId("msg-agent");
    const footer = within(agent).getByTestId("sources");
    const all = within(agent).getAllByTestId("visual");
    const lastVisual = all[all.length - 1];
    expect(lastVisual.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
