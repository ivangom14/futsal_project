import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App, { SLOW_MS } from "./App";
import contract from "../../tests/fixtures/chat_response.json";
import visualsContract from "../../tests/fixtures/chat_response_visuals.json";
import matchesJson from "../../tests/fixtures/chat_response_matches.json";
import lineupJson from "../../tests/fixtures/chat_response_match.json";
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
    const [figure] = screen.getAllByTestId("visual"); // el acta completa trae además árbitros y goles
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

describe("consultas lentas", () => {
  /** Una respuesta del chat que no llega hasta que se resuelve (o se aborta la petición). */
  function slowBackend() {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/chat/health")) return reply(HEALTH);
      return new Promise<Response>((resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        void gate.then(() => resolve(new Response(JSON.stringify(contract), { status: 200 })));
      });
    }));
    return release;
  }

  it("permite cancelar: no deja el indicador colgado y ofrece reintentar", async () => {
    slowBackend();
    render(<App />);
    await userEvent.type(screen.getByLabelText("Pregunta"), "¿Cómo va la clasificación?{Enter}");
    expect(await screen.findByText(/Futsito está consultando/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    const err = await screen.findByTestId("msg-error");
    expect(err).toHaveTextContent("Has cancelado la consulta.");
    expect(within(err).getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
    expect(screen.queryByText(/Futsito está consultando/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enviar" })).toBeDisabled(); // campo vacío, pero ya no hay una consulta en curso
    await userEvent.type(screen.getByLabelText("Pregunta"), "otra");
    expect(screen.getByRole("button", { name: "Enviar" })).toBeEnabled();
  });

  it("avisa de que está tardando más de lo normal pasados 20 s", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const release = slowBackend();
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      render(<App />);
      await user.type(screen.getByLabelText("Pregunta"), "hola{Enter}");
      await screen.findByText(/Futsito está consultando/);
      expect(screen.queryByTestId("slow-hint")).not.toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(SLOW_MS + 100);
      expect(await screen.findByTestId("slow-hint")).toHaveTextContent(/tardando más de lo normal/);
      release();
      await screen.findByTestId("msg-agent");
      expect(screen.queryByTestId("slow-hint")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("desglose del tiempo", () => {
  it("muestra dónde se fue el tiempo y las esperas por reintentos", async () => {
    const slow = { ...contract, diagnostics: { ...contract.diagnostics, duration_ms: 61_000, llm_ms: 58_000, retry_wait_ms: 40_000, llm_retries: 2,
      tools_ms: 900, context_ms: 0, postprocess_ms: 120, input_tokens_peak: 9400 } };
    backend(() => reply(slow));
    render(<App />);
    await ask("hola");
    const line = screen.getByTestId("time-breakdown");
    expect(line).toHaveTextContent("modelo 58.0 s");
    expect(line).toHaveTextContent("40.0 s esperando 2 reintento(s)");
    expect(line).toHaveTextContent("herramientas 0.9 s");
    expect(line).toHaveTextContent("montaje 0.1 s");
    expect(line).toHaveTextContent("entrada máx. 9400 tokens");
  });
});

describe("árbitros, goles y partidos (contrato real)", () => {
  const match = lineupJson as unknown as ChatResponse;

  it("el acta completa se ve como gráficos en orden: alineación, árbitros, goles y tarjetas", async () => {
    backend(() => reply(match));
    render(<App />);
    await ask("¿Cuál fue la alineación del DOSA y quién arbitró?");
    const figures = screen.getAllByTestId("visual");
    expect(figures.map((f) => within(f).getByText(/^(Alineación|Árbitros|Goles del partido|Tarjetas del partido)/).textContent)).toEqual(["Alineación · C.D. DOSA", "Árbitros", "Goles del partido", "Tarjetas del partido"]);
    expect(screen.getByText("Ganó el DOSA 3-1.")).toBeInTheDocument(); // el texto queda en una frase: lo detallado va en los gráficos
  });

  it("dibuja a cada árbitro con su monigote y su cargo del acta", async () => {
    backend(() => reply(match));
    render(<App />);
    await ask("¿Quién arbitró?");
    const refs = screen.getAllByTestId("visual")[1];
    const figs = within(refs).getAllByRole("img");
    expect(figs.map((f) => f.getAttribute("aria-label"))).toEqual([
      "Árbitro: Eduardo Hugo Iniesta del Amo", "Árbitro: Jaime Larrubia Pajares", "Árbitro asistente: Arantxa Ortiz Pinillos"]);
    expect(within(refs).getByText("Árbitro asistente")).toBeInTheDocument();
    expect(refs.querySelectorAll("svg")).toHaveLength(3);
  });

  it("muestra los goles por minuto, el gol en propia puerta a favor del rival y ninguna tarjeta", async () => {
    backend(() => reply(match));
    render(<App />);
    await ask("¿Quién marcó?");
    const goals = screen.getAllByTestId("visual")[2];
    expect(within(goals).getAllByRole("listitem")).toHaveLength(4);
    expect(within(goals).getByLabelText("minuto 20")).toBeInTheDocument();
    expect(within(goals).getByText(/A Otro Uno/)).toHaveTextContent("(en propia puerta)");
    expect(goals.textContent).not.toMatch(/tarjeta|amarilla|roja/i);
  });

  it("lista los próximos partidos con su estado y avisa de los datos posiblemente desactualizados", async () => {
    backend(() => reply(matchesJson as unknown as ChatResponse));
    render(<App />);
    await ask("¿Cuáles son los próximos partidos?");
    const card = screen.getByTestId("visual");
    expect(within(card).getByText("Próximos partidos · PRIMERA AFICIONADO FS · Grupo 2")).toBeInTheDocument();
    expect(within(card).getAllByText("Programado")).toHaveLength(3);
    expect(within(card).getAllByText("vs")).toHaveLength(3);
    expect(within(card).getByText(/El dato puede estar desactualizado|el dato puede estar desactualizado/)).toBeInTheDocument();
    expect(within(card).getByText("Sin fecha", { exact: false })).toBeInTheDocument();
    expect(within(card).getByText("Se muestran 3 de 30 partidos.")).toBeInTheDocument();
  });
});

describe("tarjetas (significado de los códigos confirmado)", () => {
  const match = lineupJson as unknown as ChatResponse;
  const cardsOf = (r: ChatResponse) => (r.visuals as unknown as { type: string }[]).findIndex((v) => v.type === "cards");

  it("dibuja amarilla, 2.ª amarilla (expulsión), roja directa y las de código desconocido sin color, con su resumen", async () => {
    backend(() => reply(match));
    render(<App />);
    await ask("¿Qué tarjetas hubo?");
    const card = screen.getAllByTestId("visual")[cardsOf(match)];
    expect(within(card).getByTestId("cards-summary")).toHaveTextContent("2 amarillas");
    expect(within(card).getByTestId("cards-summary")).toHaveTextContent("1 segunda amarilla (expulsión)");
    expect(within(card).getByTestId("cards-summary")).toHaveTextContent("1 roja directa");
    expect(within(card).getByTestId("cards-summary")).toHaveTextContent("1 tarjeta sin interpretar");
    const icons = within(card).getAllByRole("img").map((i) => i.getAttribute("aria-label"));
    expect(icons).toEqual(["Amarilla", "Amarilla", "Roja directa", "2.ª amarilla (expulsión)", "Código sin interpretar"]); // por minuto: 9, 14, 24, 30, 35
    expect(within(card).getAllByText("2.ª amarilla (expulsión)").length).toBeGreaterThan(0); // también como texto, no solo por color
    expect(within(card).getByLabelText("minuto 24")).toBeInTheDocument();
  });

  it("cuando son varios partidos lista cada tarjeta con su equipo y jornada, y avisa si la lista está recortada", async () => {
    const list = { ...match, visuals: [{ type: "cards", title: "Tarjetas", subtitle: "Según las actas importadas", home: null, away: null, partial: true,
      summary: { yellow: 1, second_yellow: 0, red: 1, unknown: 0 },
      events: [{ minute: 9, player: "B Otro Dos", team: "FS ALCALA", side: null, kind: "yellow", round: 2 },
               { minute: 24, player: "Ana Ruiz Soto", team: "C.D. DOSA", side: null, kind: "red", round: 4 }] }] };
    backend(() => reply(list));
    render(<App />);
    await ask("¿Quién ha visto más tarjetas?");
    const card = screen.getByTestId("visual");
    expect(within(card).getAllByRole("listitem")).toHaveLength(2);
    expect(within(card).getByText("J4 · 24'")).toBeInTheDocument();
    expect(within(card).getByText(/C\.D\. DOSA/)).toBeInTheDocument();
    expect(within(card).getByText(/Lista parcial/)).toBeInTheDocument();
  });
});
