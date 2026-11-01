import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import contract from "../../tests/fixtures/chat_response.json";
import visualsContract from "../../tests/fixtures/chat_response_visuals.json";
import lineupJson from "../../tests/fixtures/chat_response_lineup.json";
import type { ChatResponse } from "./types";

const HEALTH = { status: "ok", agent: { ready: true, error: null }, llm: { provider: "gemini", key_configured: true }, data_api: { reachable: true, detail: null } };
const reply = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));
const visuals = visualsContract as unknown as ChatResponse;

type Call = { url: string; method: string; body: Record<string, unknown> | null };
function backend(chat: (q: string) => Promise<Response>, feedback: () => Promise<Response> = () => reply({ saved: true })) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
    calls.push({ url, method: init?.method ?? "GET", body });
    if (url.endsWith("/chat/health")) return reply(HEALTH);
    if (url.endsWith("/chat/feedback")) return feedback();
    if (url.includes("/chat/conversations/")) return reply({ deleted: true });
    return chat(String(body?.question));
  }));
  return calls;
}
const answers = () => screen.queryAllByTestId("msg-agent").length;
const ask = async (q: string) => {
  const before = answers();
  await userEvent.type(screen.getByLabelText("Pregunta"), `${q}{Enter}`);
  await waitFor(() => expect(answers()).toBe(before + 1));
};

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("identidad Futsito", () => {
  it("muestra el logo, el nombre y la bienvenida personalizada", async () => {
    backend(() => reply(contract));
    render(<App />);
    expect(screen.getAllByAltText("Futsito").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Futsito");
    expect(screen.getByText(/Soy Futsito/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Estado: Conectado|Estado: Comprobando/ })).toBeInTheDocument();
  });
});

describe("visuales estructurados (contrato real)", () => {
  it("renderiza tabla, comparativa, ranking y forma sin interpretar texto", async () => {
    backend(() => reply(visuals));
    render(<App />);
    await ask("Compara el DOSA y el Alcalá en la clasificación");
    const figures = screen.getAllByTestId("visual");
    expect(figures).toHaveLength(4);
    const table = within(figures[0]).getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Pos", "Equipo", "PJ", "G", "E", "P", "GF", "GC", "DG", "Pts"]);
    expect(within(table).getByText("C.D. DOSA").closest("tr")).toHaveClass("font-semibold"); // equipo mencionado resaltado
    expect(within(figures[1]).getByText("Puntos")).toBeInTheDocument();
    expect(within(figures[2]).getByRole("img", { name: "Juan Pérez: 9" })).toBeInTheDocument();
    expect(within(figures[3]).getByRole("img", { name: /Puntos acumulados: 3, 4, 4/ })).toBeInTheDocument();
    expect(screen.getByText(/El DOSA lidera con 13 puntos/)).toBeInTheDocument(); // la respuesta de texto sigue estando
  });

  it("sin visuales no dibuja nada y un tipo desconocido se ignora sin romper", async () => {
    backend(() => reply({ ...contract, visuals: [{ type: "pie", title: "x" }] }));
    render(<App />);
    await ask("hola");
    expect(screen.queryByTestId("visual")).not.toBeInTheDocument();
  });

  it("no ejecuta HTML que llegue en los datos", async () => {
    const evil = { ...visuals, visuals: [{ type: "bars", title: "<img src=x onerror=alert(1)>", rows: [{ label: "<b>x</b>", value: 1 }] }] };
    backend(() => reply(evil));
    render(<App />);
    await ask("x");
    expect(document.querySelector("img[src='x']")).toBeNull();
    expect(screen.getByText("<b>x</b>", { exact: false })).toBeInTheDocument();
  });
});

describe("fuentes y frescura", () => {
  it("muestra fecha de actualización de los datos oficiales y avisa cuando no se conoce", async () => {
    const withUnknown = { ...visuals, sources: [...visuals.sources, { ...visuals.sources[0], title: "Actas de partidos · RFFM", updated_at: null, freshness: "unknown",
      updated_note: "No se conoce la fecha de actualización de estos datos." }] };
    backend(() => reply(withUnknown));
    render(<App />);
    await ask("Compara");
    const region = screen.getByRole("region", { name: "Fuentes" });
    expect(within(region).getByText("Datos oficiales RFFM")).toBeInTheDocument();
    await userEvent.click(within(region).getByText(/Clasificación oficial · RFFM/));
    expect(within(region).getAllByText(/Datos actualizados el 28\/10\/2026/)).toHaveLength(2); // clasificación y goleadores
    await userEvent.click(within(region).getByText(/Actas de partidos · RFFM/));
    expect(within(region).getByText("No se conoce la fecha de actualización de estos datos.")).toBeInTheDocument();
  });

  it("identifica el análisis como no oficial y etiqueta el origen", async () => {
    backend(() => reply({ ...visuals, is_analysis: true }));
    render(<App />);
    await ask("Analiza al DOSA");
    expect(screen.getByText(/Análisis o recomendación · no es información oficial/)).toBeInTheDocument();
    expect(screen.getByText("Datos oficiales RFFM", { selector: "span" })).toBeInTheDocument();
  });
});

describe("correspondencia respuesta-fuentes", () => {
  it("los fragmentos no citados quedan aparte y plegados, no como respaldo de la respuesta", async () => {
    const [first, second] = contract.sources;
    backend(() => reply({ ...contract, sources: [{ ...first, cited: true }, { ...second, cited: false, pages: "99" }] }));
    render(<App />);
    await ask("hola");
    const region = screen.getByRole("region", { name: "Fuentes" });
    expect(within(region).getByText("Normativa citada")).toBeInTheDocument();
    const others = within(region).getByText(/Otros fragmentos consultados, no citados en la respuesta \(1\)/);
    expect(others.closest("details")).not.toHaveAttribute("open");
  });
});

describe("preguntas sugeridas", () => {
  it("se envían como mensajes normales y solo la última respuesta las ofrece", async () => {
    const calls = backend((q) => reply(q.startsWith("¿Quiénes") ? { ...contract, suggestions: [] } : visuals));
    render(<App />);
    await ask("Compara el DOSA y el Alcalá en la clasificación");
    const group = screen.getByRole("group", { name: "Preguntas sugeridas" });
    expect(within(group).getAllByRole("button")).toHaveLength(3);
    await userEvent.click(within(group).getByRole("button", { name: "¿Quiénes son los próximos rivales de C.D. DOSA?" }));
    await waitFor(() => expect(answers()).toBe(2));
    const chats = calls.filter((c) => c.url.endsWith("/chat"));
    expect(chats[1].body?.question).toBe("¿Quiénes son los próximos rivales de C.D. DOSA?");
    expect(chats[1].body?.conversation_id).toBe(chats[0].body?.conversation_id);
    expect(screen.getAllByTestId("msg-user")[1]).toHaveTextContent("¿Quiénes son los próximos rivales de C.D. DOSA?");
    expect(screen.queryByRole("group", { name: "Preguntas sugeridas" })).not.toBeInTheDocument();
  });
});

describe("foco de la conversación", () => {
  it("reenvía el foco de la última respuesta válida y no mezcla conversaciones", async () => {
    const calls = backend(() => reply({ ...contract, focus: { team: "C.D. DOSA", group: "Grupo 2" } }));
    render(<App />);
    await ask("¿Cómo va el DOSA?");
    await ask("¿Y el siguiente?");
    await userEvent.click(screen.getByRole("button", { name: "+ Nueva conversación" }));
    await ask("¿Y el siguiente?");
    const chats = calls.filter((c) => c.url.endsWith("/chat"));
    expect(chats[0].body?.focus).toEqual({});
    expect(chats[1].body?.focus).toEqual({ team: "C.D. DOSA", group: "Grupo 2" });
    expect(chats[2].body?.focus).toEqual({});
  });
});

describe("valoración de respuestas", () => {
  it("👍 envía la valoración asociada al mensaje y la conversación", async () => {
    const calls = backend(() => reply(contract));
    render(<App />);
    await ask("hola");
    await userEvent.click(screen.getByRole("button", { name: "Respuesta útil" }));
    expect(await screen.findByText(/Gracias por tu valoración/)).toBeInTheDocument();
    const fb = calls.find((c) => c.url.endsWith("/chat/feedback"))!;
    const chat = calls.find((c) => c.url.endsWith("/chat") && c.method === "POST")!;
    expect(fb.body).toMatchObject({ rating: "up", reason: null, comment: null, run_id: contract.diagnostics.run_id, conversation_id: chat.body?.conversation_id });
    expect(typeof fb.body?.message_id).toBe("string");
  });

  it("👎 pide uno de los seis motivos y un comentario opcional", async () => {
    const calls = backend(() => reply(contract));
    render(<App />);
    await ask("hola");
    await userEvent.click(screen.getByRole("button", { name: "Respuesta mejorable" }));
    expect(screen.getAllByRole("radio").map((r) => r.parentElement?.textContent?.trim())).toEqual([
      "Dato incorrecto", "Ha perdido el contexto", "Respuesta incompleta", "Fuente incorrecta o no respaldada", "Ha interpretado mal la pregunta", "Otro"]);
    expect(screen.getByRole("button", { name: "Enviar valoración" })).toBeDisabled(); // sin motivo no se envía
    await userEvent.click(screen.getByLabelText("Ha perdido el contexto"));
    await userEvent.type(screen.getByPlaceholderText("Comentario (opcional)"), "No recordó el equipo");
    await userEvent.click(screen.getByRole("button", { name: "Enviar valoración" }));
    expect(await screen.findByText(/lo tendremos en cuenta \(Ha perdido el contexto\)/)).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/chat/feedback"))!.body).toMatchObject({ rating: "down", reason: "lost_context", comment: "No recordó el equipo" });
  });

  it("persiste tras recargar y un fallo al guardar no la da por enviada", async () => {
    let ok = false;
    backend(() => reply(contract), () => (ok ? reply({ saved: true }) : reply({ detail: "x" }, 500)));
    const first = render(<App />);
    await ask("hola");
    await userEvent.click(screen.getByRole("button", { name: "Respuesta útil" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/No se ha podido guardar/);
    expect(screen.queryByText(/Gracias por tu valoración/)).not.toBeInTheDocument();
    ok = true;
    await userEvent.click(screen.getByRole("button", { name: "Respuesta útil" }));
    await screen.findByText(/Gracias por tu valoración/);
    first.unmount();
    render(<App />);
    expect(screen.getByText(/Gracias por tu valoración/)).toBeInTheDocument();
  });
});

describe("alineaciones", () => {
  const lineupContract = lineupJson as unknown as ChatResponse;

  it("dibuja el quinteto en la pista y los suplentes a un lado, con el contrato real", async () => {
    backend(() => reply(lineupContract));
    render(<App />);
    await ask("¿Cuál fue la alineación del DOSA?");
    const figure = screen.getByTestId("visual");
    expect(within(figure).getByText("Alineación · C.D. DOSA")).toBeInTheDocument();
    const court = within(figure).getByRole("img", { name: /^Quinteto inicial de C\.D\. DOSA: 1 Javier Aparicio Jimenez, 5 Pedro Gil Mora/ });
    expect(within(court as unknown as HTMLElement).getAllByText(/^[A-Z]\. /)).toHaveLength(5); // 5 titulares con su etiqueta
    expect(within(court as unknown as HTMLElement).getByText("Portero")).toBeInTheDocument();
    const subs = within(figure).getByRole("region", { name: "Suplentes" });
    const items = within(subs).getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(items).toHaveLength(2);
    expect(items[0]).toContain("Sara Reyes Vega"); // portero suplente primero
    expect(items[1]).toContain("Luis Suplente Garcia");
    expect(within(figure).getByText(/la colocación en el campo es orientativa/)).toBeInTheDocument(); // no inventa posiciones
    expect(screen.queryByText(/Cierre|Pívot|Pivote|Ala /)).not.toBeInTheDocument();
  });

  it("sin cinco titulares y un portero no dibuja la pista: lista los titulares y lo avisa", async () => {
    const [v] = lineupContract.visuals as unknown as { starters: unknown[] }[];
    const broken = { ...lineupContract, visuals: [{ ...v, court: false, starters: v.starters.slice(0, 4) }] };
    backend(() => reply(broken));
    render(<App />);
    await ask("alineación");
    const figure = screen.getByTestId("visual");
    expect(within(figure).queryByRole("img", { name: /Quinteto inicial/ })).not.toBeInTheDocument();
    expect(within(figure).getByRole("region", { name: "Titulares" })).toBeInTheDocument();
    expect(within(figure).getByText(/no permite dibujar un quinteto completo/)).toBeInTheDocument();
  });
});
