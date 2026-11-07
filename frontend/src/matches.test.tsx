import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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
  it("muestra la hora de Madrid junto a la fecha («dom 08/11 · 11:00») y la jornada", () => {
    render(<Matches v={visual([row("C.D. DOSA", "CLUB X")])} />);
    expect(screen.getByTestId("match-when")).toHaveTextContent("Jornada 7 · dom 08/11 · 11:00");
  });
  it("sin hora en el dato solo muestra la fecha; sin fecha, «Sin fecha»", () => {
    render(<Matches v={visual([row("A", "B", { date: "2026-11-08" }), row("C", "D", { date: null })])} />);
    const when = screen.getAllByTestId("match-when").map((e) => e.textContent);
    expect(when).toEqual(["Jornada 7 · dom 08/11", "Jornada 7 · Sin fecha"]);
  });
  it("los nombres se muestran completos (cortos y largos), con el texto íntegro también en `title`, y nunca como puntos suspensivos", () => {
    const rows = [row("C.D. DOSA", "CLUB X"), row(LONG, "COLEGIO CARDENAL SPINOLA CHAMARTIN 'B'"), row("SAN FELIPE NERI - ELIPA DRAGONS 017", "ESC. DEP. DE FUTBOL DE TORRES")];
    render(<Matches v={visual(rows)} />);
    for (const r of rows) {
      for (const name of [r.home, r.away]) {
        const el = screen.getAllByText(name)[0];
        expect(el).toHaveTextContent(name);
        expect(el).toHaveAttribute("title", name);
        expect(el.className).not.toMatch(/\btruncate\b|text-ellipsis/);
        expect(el.textContent).not.toMatch(/…|\.\.\.$/);
      }
    }
  });
  it("cada partido conserva el orden local · VS (o marcador) · visitante, que es también el orden de lectura", () => {
    render(<Matches v={visual([row("LOCAL", "VISITANTE"), row("A", "B", { score: "7-1", status: "finished" })])} />);
    const [first, second] = screen.getAllByTestId("match-row");
    const order = (el: HTMLElement) => Array.from(el.querySelectorAll(".match-row > span")).map((s) => s.textContent);
    expect(order(first)).toEqual(["LOCAL", "vs", "VISITANTE"]);
    expect(order(second)).toEqual(["A", "7-1", "B"]);
    expect(first.querySelector(".match-home")).not.toBeNull();
    expect(first.querySelector(".match-mid")).not.toBeNull();
    expect(first.querySelector(".match-away")).not.toBeNull();
  });
  it("usa el contenedor de consulta (el diseño depende del ancho de la tarjeta, no de la pantalla)", () => {
    const { container } = render(<Matches v={visual([row("A", "B")])} />);
    expect(container.querySelector("ul.match-list")).not.toBeNull();
  });
  it("sigue avisando de fecha pasada, partidos parciales y estados distintos", () => {
    render(<Matches v={{ ...visual([row("A", "B", { past_due: true }), row("C", "D", { status: "finished", score: "1-0" })]), partial: true, total: 30 }} />);
    expect(screen.getByText(/ya pasó y sigue como programado/)).toBeInTheDocument();
    expect(screen.getByText("Finalizado")).toBeInTheDocument();
    expect(screen.getByText("Se muestran 2 de 30 partidos.")).toBeInTheDocument();
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
  it("sin comprensión de contexto (legacy: null) no aparece el bloque", () => {
    render(<TechnicalDetails response={base} />);
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
