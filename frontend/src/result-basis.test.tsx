import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Matches from "./components/Matches";
import { resultBasisLabel } from "./format";
import type { MatchesVisual } from "./types";

// Fase 3E: un marcador administrativo (equipo retirado), por resolución, provisional o con el acta sin cerrar NO es un partido disputado
const row = (over: Partial<MatchesVisual["rows"][number]> = {}) =>
  ({ round: 11, date: "2026-11-29T11:00:00+00:00", home: "EQUIPO RETIRADO", away: "RIVAL", score: "0-3", status: "unknown", past_due: false, ...over });
const visual = (rows: MatchesVisual["rows"]): MatchesVisual => ({ type: "matches", title: "Partidos", subtitle: null, total: rows.length, partial: false, rows });

describe("base del marcador en la tarjeta de partidos", () => {
  it("un resultado administrativo se marca como no disputado y sin ganador resaltado", () => {
    render(<Matches v={visual([row({ basis: "withdrawal_home" })])} />);
    expect(screen.getByTestId("match-basis")).toHaveTextContent("Resultado administrativo: el local se ha retirado · no se disputa");
    const goals = screen.getAllByText("3");
    expect(goals[0].className).not.toContain("font-extrabold"); // sin «ganador»: no consta que se jugara
    expect(screen.getByTestId("match-row").textContent).toContain("no se disputa"); // también para lectores de pantalla
  });
  it("cada base tiene su texto; un partido normal no lleva nota y conserva el ganador en negrita", () => {
    expect(resultBasisLabel("resolution")).toContain("resolución de la RFFM");
    expect(resultBasisLabel("unconfirmed")).toContain("Acta sin cerrar");
    expect(resultBasisLabel("provisional")).toContain("provisional");
    expect(resultBasisLabel("suspended")).toContain("suspendido");
    expect(resultBasisLabel(null)).toBeNull();
    render(<Matches v={visual([row({ status: "finished", score: "2-1", home: "A", away: "B" })])} />);
    expect(screen.queryByTestId("match-basis")).toBeNull();
    expect(screen.getByText("2").className).toContain("font-extrabold");
  });
});
