import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import MessageBubble, { LONG_ANSWER, tidyAnswer } from "./components/MessageBubble";
import Sidebar, { groupByRecency, matchesQuery, SEARCH_FROM } from "./components/Sidebar";
import ConnectionBadge from "./components/ConnectionBadge";
import Composer from "./components/Composer";
import { dayHeading, parseScore } from "./format";
import type { ChatResponse, Conversation, Message } from "./types";
import contract from "../../tests/fixtures/chat_response.json";

const base = contract as unknown as ChatResponse;
const row = (i: number) => ({ round: null, date: `2026-11-21T1${i % 10}:00`, home: `L${i}`, away: `V${i}`, score: null, status: "scheduled", past_due: false, detail: "PREFERENTE AFICIONADO FS" });
const paged: ChatResponse = {
  ...base, answer_kind: "official",
  answer: "Este fin de semana hay **48 partidos** de categoría sénior. Mostrando 1–10 de 48.\n\n¿Quieres que te muestre los siguientes 10?",
  visuals: [{ type: "matches", title: "Partidos sénior", subtitle: "Mostrando 1–10 de 48", total: 48, partial: true, rows: Array.from({ length: 10 }, (_, i) => row(i)) }],
  suggestions: ["Muéstrame los siguientes 10"],
};
const msg = (response: ChatResponse, content = response.answer): Message => ({ id: "m1", role: "assistant", content, createdAt: 0, response });

afterEach(() => vi.unstubAllEnvs());

describe("respuestas paginadas", () => {
  it("el texto no repite el rango ni la pregunta de paginación que ya muestra la tarjeta; el resto se conserva", () => {
    expect(tidyAnswer(paged.answer, true)).toBe("Este fin de semana hay **48 partidos** de categoría sénior.");
    expect(tidyAnswer(paged.answer, false)).toBe(paged.answer); // sin visual paginado, nada se toca
  });
  it("«Muéstrame los siguientes 10» pasa de chip suelto a acción de la tarjeta y envía la misma petición (sin LLM en el backend)", async () => {
    const onSuggest = vi.fn();
    render(<MessageBubble message={msg(paged)} onSuggest={onSuggest} onFeedback={() => undefined} />);
    expect(screen.queryByRole("group", { name: "Preguntas sugeridas" })).toBeNull();
    expect(screen.getByTestId("msg-agent")).not.toHaveTextContent("¿Quieres que te muestre");
    await userEvent.click(screen.getByTestId("matches-more"));
    expect(onSuggest).toHaveBeenCalledWith("Muéstrame los siguientes 10");
  });
  it("en una respuesta antigua (no la última) no hay acción, pero sí se dice cuántos hay", () => {
    render(<MessageBubble message={msg(paged)} onFeedback={() => undefined} />);
    expect(screen.queryByTestId("matches-more")).toBeNull();
    expect(screen.getByText(/Se muestran 10 de 48 partidos/)).toBeInTheDocument();
  });
});

describe("respuestas largas y tablas del agente", () => {
  it("una respuesta muy larga se pliega con «Mostrar respuesta completa» (el texto completo sigue en el DOM)", async () => {
    const long = "Texto. ".repeat(Math.ceil(LONG_ANSWER / 7) + 10);
    render(<MessageBubble message={msg({ ...base, answer: long })} onFeedback={() => undefined} />);
    expect(screen.getByTestId("answer-clamp")).toHaveClass("answer-clamp");
    await userEvent.click(screen.getByRole("button", { name: "Mostrar respuesta completa" }));
    expect(screen.getByTestId("answer-clamp")).not.toHaveClass("answer-clamp");
  });
  it("las tablas Markdown van en un contenedor con desplazamiento propio (nunca desbordan la página)", () => {
    const md = "| Jugador | Juvenil | Sénior |\n|---|---:|---:|\n| Hugo | 8 | 3 |";
    const { container } = render(<MessageBubble message={msg({ ...base, answer: md })} onFeedback={() => undefined} />);
    const wrap = container.querySelector(".md-table");
    expect(wrap).toHaveClass("overflow-x-auto");
    expect(within(wrap as HTMLElement).getByRole("table")).toBeInTheDocument();
  });
});

const conv = (id: string, title: string, updatedAt: number, q = title): Conversation => ({
  id, title, createdAt: updatedAt, updatedAt, messages: [{ id: `${id}u`, role: "user", content: q, createdAt: updatedAt }],
});

describe("historial", () => {
  const now = new Date(2026, 10, 17, 12).getTime();
  it("agrupa por última actividad: Hoy, Ayer, Últimos 7 días, Últimos 30 días, Anteriores", () => {
    const day = 86_400_000;
    const groups = groupByRecency([conv("a", "A", now - 1000), conv("b", "B", now - day), conv("c", "C", now - 3 * day), conv("d", "D", now - 20 * day), conv("e", "E", now - 90 * day)], now);
    expect(groups.map((g) => g.label)).toEqual(["Hoy", "Ayer", "Últimos 7 días", "Últimos 30 días", "Anteriores"]);
  });
  it("la búsqueda ignora tildes y mayúsculas y mira también las preguntas, no solo el título", () => {
    const c = conv("a", "Clasificación", now, "¿Cómo va el Lope de Vega?");
    expect(matchesQuery(c, "clasificacion")).toBe(true);
    expect(matchesQuery(c, "lope vega")).toBe(true);
    expect(matchesQuery(c, "dosa")).toBe(false);
  });
  it("con muchas conversaciones ofrece búsqueda; la vacía no se lista; borrar está en un menú y se cierra con Escape", async () => {
    const list = Array.from({ length: SEARCH_FROM }, (_, i) => conv(`c${i}`, `Conversación ${i}`, now - i));
    const empty: Conversation = { id: "new", title: "Nueva conversación", createdAt: now, updatedAt: now, messages: [] };
    const onDelete = vi.fn();
    const onSelectSpy = vi.fn();
    render(<Sidebar conversations={[empty, ...list]} activeId="c0" open={false} onNew={() => undefined} onSelect={onSelectSpy} onDelete={onDelete} onClose={() => undefined} />);
    expect(screen.getAllByRole("button", { name: /^Conversación \d$/ })).toHaveLength(SEARCH_FROM);
    expect(screen.getByRole("button", { name: "Conversación 0" })).toHaveAttribute("aria-current", "page");
    await userEvent.type(screen.getByRole("searchbox", { name: "Buscar conversaciones" }), "3");
    expect(screen.getAllByRole("button", { name: /^Conversación \d$/ })).toHaveLength(1);
    // papelera directa (sin menú «⋯»): borra sin abrir la conversación
    expect(screen.queryByRole("button", { name: /Acciones de la conversación/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Borrar conversación Conversación 3" }));
    expect(onSelectSpy).not.toHaveBeenCalled();
    expect(onDelete).toHaveBeenCalledWith("c3");
  });
});

describe("cabecera y compositor", () => {
  it("el estado del servicio solo se ve cuando hay un problema", () => {
    vi.stubEnv("VITE_SHOW_DIAGNOSTICS", "0");
    const health = { status: "ok" as const, agent: { ready: true, error: null }, llm: { provider: "x", key_configured: true }, data_api: { reachable: true, detail: null } };
    const { rerender } = render(<ConnectionBadge connection={{ state: "ok", health }} onRefresh={() => undefined} />);
    expect(screen.queryByRole("button")).toBeNull();
    rerender(<ConnectionBadge connection={{ state: "offline", detail: "Sin respuesta" }} onRefresh={() => undefined} />);
    expect(screen.getByRole("button", { name: /Estado: Sin conexión/ })).toBeInTheDocument();
  });
  it("sin foco no hay rótulo de contexto; con foco, «Sobre …» y una × para empezar sin él", async () => {
    const clear = vi.fn();
    const { rerender } = render(<Composer onSend={() => undefined} loading={false} onClearContext={clear} />);
    expect(screen.queryByTestId("context-hint")).toBeNull();
    rerender(<Composer onSend={() => undefined} loading={false} focus={{ team: "C.D. DOSA" }} onClearContext={clear} />);
    expect(screen.getByTestId("context-hint")).toHaveTextContent("Sobre C.D. DOSA");
    await userEvent.click(screen.getByRole("button", { name: /Quitar el contexto \(C\.D\. DOSA\)/ }));
    expect(clear).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("Pregunta")).toHaveAttribute("enterkeyhint", "send");
  });
});

describe("formato", () => {
  it("cabecera de día en Madrid y marcadores solo si tienen forma «n-m»", () => {
    expect(dayHeading("2026-11-21T10:00")).toBe("Sábado, 21 de noviembre");
    expect(dayHeading("2026-11-21T23:30:00+00:00")).toBe("Domingo, 22 de noviembre");
    expect(dayHeading(null)).toBeNull();
    expect(parseScore("7-1")).toEqual([7, 1]);
    expect(parseScore("3 – 3")).toEqual([3, 3]);
    expect(parseScore("3-3 (p. 4-2)")).toBeNull();
    expect(parseScore(null)).toBeNull();
  });
});
