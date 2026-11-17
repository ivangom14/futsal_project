import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Matches from "./components/Matches";
import TechnicalDetails from "./components/TechnicalDetails";
import Visuals from "./components/Visuals";
import type { ChatResponse, MatchesVisual, Visual } from "./types";
import contract from "../../tests/fixtures/chat_response.json";

const LONG = "C.D.B. MORALZARZAL FUTSAL - TALLERES LOMILLOS BERROCOSA 'A'"; // el más largo del catálogo real (60 caracteres)
const row = (home: string, away: string, over: Partial<MatchesVisual["rows"][number]> = {}) =>
  ({ round: 7, date: "2026-11-08T10:00:00+00:00", home, away, score: null, status: "scheduled", past_due: false, ...over });
const visual = (rows: MatchesVisual["rows"]): MatchesVisual => ({ type: "matches", title: "Próximos partidos · Grupo 2", subtitle: null, total: rows.length, partial: false, rows });

describe("tarjeta de partidos", () => {
  it("una fila por partido: fecha y hora de Madrid a la izquierda; la jornada común se dice una vez en la cabecera", () => {
    render(<Matches v={visual([row("C.D. DOSA", "CLUB X"), row("A", "B", { date: "2026-11-15T10:00:00+00:00" })])} />);
    expect(screen.getAllByTestId("match-when")[0]).toHaveTextContent("dom 08/1111:00");
    expect(screen.getByTestId("visual")).toHaveTextContent("Jornada 7 · Todos programados");
    expect(screen.queryByTestId("match-status")).toBeNull(); // «Programado» no se repite en cada fila
  });
  it("varios partidos el mismo día: se agrupan bajo el día y, si hay varias competiciones, bajo cada competición (orden horario dentro)", () => {
    const rows = [row("A", "B", { date: "2026-11-14T10:00", detail: "PREFERENTE" }), row("C", "D", { date: "2026-11-14T11:00", detail: "PRIMERA" }),
      row("E", "F", { date: "2026-11-14T12:00", detail: "PREFERENTE" }), row("G", "H", { date: "2026-11-15T10:00", detail: "PRIMERA" })];
    const v = { ...visual(rows), subtitle: "Mostrando 11–14 de 48", total: 48, partial: true };
    render(<Matches v={v} />);
    const days = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(days).toEqual(["Sábado, 14 de noviembre", "Domingo, 15 de noviembre"]);
    const comps = screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent);
    expect(comps).toEqual(["PREFERENTE", "PRIMERA", "PRIMERA"]);
    const order = screen.getAllByTestId("match-row").map((r) => r.querySelector(".match-team")?.textContent);
    expect(order).toEqual(["A", "E", "C", "G"]);
    expect(screen.getByText(/Mostrando 11–14 de 48/)).toBeTruthy();
  });
  it("sin hora en el dato no inventa una («—» o solo la fecha); sin fecha, «Sin fecha»", () => {
    render(<Matches v={visual([row("A", "B", { date: "2026-11-08" }), row("C", "D", { date: null })])} />);
    const when = screen.getAllByTestId("match-when").map((e) => e.textContent);
    expect(when).toEqual(["dom 08/11", "Sin fecha"]);
  });
  it("los nombres se muestran completos (cortos y largos), con el texto íntegro también en `title`, y nunca como puntos suspensivos", () => {
    const rows = [row("C.D. DOSA", "CLUB X"), row(LONG, "COLEGIO CARDENAL SPINOLA CHAMARTIN 'B'"), row("SAN FELIPE NERI - ELIPA DRAGONS 017", "ESC. DEP. DE FUTBOL DE TORRES")];
    render(<Matches v={visual(rows)} />);
    for (const r of rows) {
      for (const name of [r.home, r.away]) {
        const el = screen.getAllByText(name, { selector: ".match-team" })[0];
        expect(el).toHaveTextContent(name);
        expect(el).toHaveAttribute("title", name);
        expect(el.className).not.toMatch(/\btruncate\b|text-ellipsis/);
        expect(el.textContent).not.toMatch(/…|\.\.\.$/);
      }
    }
  });
  it("marcador por equipo con el ganador destacado; local arriba y visitante abajo; una frase completa para lectores de pantalla", () => {
    render(<Matches v={visual([row("LOCAL", "VISITANTE"), row("A", "B", { score: "7-1", status: "finished" })])} />);
    const [first, second] = screen.getAllByTestId("match-row");
    const teams = (el: HTMLElement) => Array.from(el.querySelectorAll(".match-team")).map((s) => s.textContent);
    expect(teams(first)).toEqual(["LOCAL", "VISITANTE"]);
    const goals = Array.from(second.querySelectorAll(".match-goals")).map((g) => g.textContent);
    expect(goals).toEqual(["7", "1"]);
    expect(second.querySelectorAll(".match-team")[0].className).toMatch(/font-semibold/);
    expect(second.querySelector(".sr-only")).toHaveTextContent("A 7, B 1. Finalizado.");
    expect(first.querySelector(".sr-only")).toHaveTextContent("LOCAL contra VISITANTE. Programado.");
  });
  it("un marcador con otro formato se muestra tal cual, sin reinterpretarlo", () => {
    render(<Matches v={visual([row("A", "B", { score: "3-3 (p. 4-2)", status: "finished" }), row("C", "D")])} />);
    expect(screen.getByTestId("match-score")).toHaveTextContent("3-3 (p. 4-2)");
  });
  it("usa el contenedor de consulta (el diseño depende del ancho de la tarjeta, no de la pantalla)", () => {
    const { container } = render(<Matches v={visual([row("A", "B")])} />);
    expect(container.querySelector("ul.match-list")).not.toBeNull();
  });
  it("avisa de fecha pasada, de estados excepcionales por fila y de que hay más partidos", () => {
    render(<Matches v={{ ...visual([row("A", "B", { past_due: true }), row("C", "D", { status: "postponed" }), row("E", "F", { status: "" })]), partial: true, total: 30 }} />);
    expect(screen.getByText(/ya pasó y sigue como programado/)).toBeInTheDocument();
    expect(screen.getByText("Aplazado")).toBeInTheDocument();
    expect(screen.getByText("Sin confirmar")).toHaveAttribute("title", "Estado en la fuente: vacío");
    expect(screen.getByText(/Se muestran 3 de 30 partidos/)).toBeInTheDocument();
    expect(screen.getByTestId("matches-count")).toHaveTextContent("3 de 30");
  });
  it("más de 10 filas recibidas: muestra 10 y despliega el resto sin pedir nada al servidor", async () => {
    const rows = Array.from({ length: 12 }, (_, i) => row(`L${i}`, `V${i}`, { date: `2026-11-${String(10 + i).padStart(2, "0")}T10:00:00+00:00` }));
    render(<Matches v={visual(rows)} />);
    expect(screen.getAllByTestId("match-row")).toHaveLength(10);
    await userEvent.click(screen.getByTestId("matches-expand"));
    expect(screen.getAllByTestId("match-row")).toHaveLength(12);
  });
  it("listado paginado del backend: «Ver más» pide la página siguiente (paginación conversacional sin LLM)", async () => {
    const onMore = vi.fn();
    render(<Matches v={{ ...visual([row("A", "B"), row("C", "D")]), subtitle: "Mostrando 1–2 de 48", total: 48, partial: true }} onMore={onMore} />);
    await userEvent.click(screen.getByTestId("matches-more"));
    expect(onMore).toHaveBeenCalledOnce();
    expect(screen.getByTestId("matches-more")).toHaveTextContent("Ver 10 partidos más · quedan 46");
  });
});

describe("coherencia con el resto de visuales: sin nombres cortados", () => {
  it("la cabecera de goles, la de tarjetas y la forma del equipo envuelven los nombres en vez de truncarlos", () => {
    const visuals: Visual[] = [
      { type: "timeline", title: "Goles", subtitle: null, home: LONG, away: "CLUB X", partial: false, events: [{ minute: 5, player: "P", side: "home", own_goal: false }] },
      { type: "cards", title: "Tarjetas", subtitle: null, home: LONG, away: "CLUB X", partial: false, summary: { yellow: 0, second_yellow: 0, red: 0, unknown: 0 }, events: [{ minute: 3, player: "P", team: null, side: "home", kind: "yellow", round: null }] },
      { type: "form", title: "Forma", subtitle: null, rows: [{ round: 1, rival: LONG, score: "1-0", result: "V", points: 3, home: true }] },
    ];
    const { container } = render(<Visuals visuals={visuals} />);
    expect(within(container).getAllByText(LONG, { exact: false }).length).toBeGreaterThanOrEqual(3);
    expect(container.querySelectorAll(".truncate").length).toBe(0);
  });
});

describe("comprensión de contexto en «Detalles técnicos»", () => {
  const base = contract as unknown as ChatResponse;
  it("muestra modo, estado, intención, competición, suposición, intérprete y recuperación; en legacy (null) no muestra nada", () => {
    const response: ChatResponse = {
      ...base,
      diagnostics: { ...base.diagnostics, context: { mode: "llm", state: "rebuilt", status: "assume", intent: "standings", competitions: ["PREFERENTE JUVENIL FS"],
        provenance: { scope: "age" }, assumption: "Entiendo que te refieres a PREFERENTE JUVENIL FS.", interpreter_trigger: "ambiguous_scope",
        interpreter: { competitions: ["PREFERENTE JUVENIL FS"], confidence: "high" }, recovery: "revision", options: ["A", "B"] } },
    };
    render(<TechnicalDetails response={response} />);
    const box = screen.getByTestId("context-info");
    expect(box).toHaveTextContent("Comprensión (llm): assume · intención standings → PREFERENTE JUVENIL FS · ámbito age · estado reconstruido del historial");
    expect(box).toHaveTextContent("Suposición declarada: Entiendo que te refieres a PREFERENTE JUVENIL FS.");
    expect(box).toHaveTextContent("Intérprete LLM consultado (ambiguous_scope) → PREFERENTE JUVENIL FS");
    expect(box).toHaveTextContent("Opciones ofrecidas: A; B");
    expect(box).toHaveTextContent("Recuperación: revision");
  });
  it("sin comprensión de contexto (context: null) no aparece el bloque", () => {
    render(<TechnicalDetails response={{ ...base, diagnostics: { ...base.diagnostics, context: null } }} />);
    expect(screen.queryByTestId("context-info")).toBeNull();
  });
});

describe("trazabilidad de la conciliación en «Detalles técnicos»", () => {
  const base = contract as unknown as ChatResponse;
  it("muestra cuántos elementos se omitieron, cuáles y por qué, y cuáles se conservaron por falta de certeza", () => {
    const response: ChatResponse = {
      ...base,
      diagnostics: { ...base.diagnostics, reconciliation: { considered: 3, removed: 2, kept: 1, pointer_added: false,
        removed_items: [{ kind: "list_item", reason: "matches: lo respaldan 1 registro(s) del visual (A vs B)", snippet: "A vs B (08/11/2026 a las 11:00)" }],
        kept_uncertain: [{ kind: "list_item", reason: "matches: contiene datos que el visual no tiene", left: ["pista"], snippet: "C vs D en pista 3" }] } },
    };
    render(<TechnicalDetails response={response} />);
    const box = screen.getByTestId("reconciliation");
    expect(box).toHaveTextContent("2 de 3 elementos de lista o tabla se omitieron");
    expect(box).toHaveTextContent("Omitido: «A vs B (08/11/2026 a las 11:00)» · matches:");
    expect(box).toHaveTextContent("Conservado: «C vs D en pista 3»");
    expect(box).toHaveTextContent("no respaldado: pista");
  });
  it("si la conciliación falló, lo dice y avisa de que se muestra el texto original", () => {
    const response: ChatResponse = { ...base, diagnostics: { ...base.diagnostics, reconciliation: { considered: 0, removed: 0, kept: 0, pointer_added: false, removed_items: [], kept_uncertain: [], error: "TypeError" } } };
    render(<TechnicalDetails response={response} />);
    expect(screen.getByTestId("reconciliation")).toHaveTextContent("La conciliación falló (TypeError): se muestra el texto original del agente.");
  });
  it("sin conciliación (el contrato real: null) no muestra nada", () => {
    render(<TechnicalDetails response={base} />);
    expect(screen.queryByTestId("reconciliation")).toBeNull();
  });
});
