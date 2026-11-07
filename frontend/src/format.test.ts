import { describe, expect, it } from "vitest";
import { formatKickoff, kickoffLabel } from "./format";

describe("fecha y hora de un partido en Europe/Madrid", () => {
  it("convierte a la hora de Madrid con independencia de la zona del navegador (invierno, UTC+1)", () => {
    expect(kickoffLabel("2026-11-08T10:00:00+00:00")).toBe("dom 08/11 · 11:00");
    expect(kickoffLabel("2026-11-07T10:00:00+00:00")).toBe("sáb 07/11 · 11:00");
  });
  it("usa el horario de verano (UTC+2) y los cambios de hora exactos", () => {
    expect(kickoffLabel("2026-07-15T10:00:00+00:00")).toBe("mié 15/07 · 12:00");
    expect(kickoffLabel("2026-03-29T00:59:00+00:00")).toBe("dom 29/03 · 01:59");
    expect(kickoffLabel("2026-03-29T01:00:00+00:00")).toBe("dom 29/03 · 03:00");
    expect(kickoffLabel("2026-10-25T00:59:00+00:00")).toBe("dom 25/10 · 02:59");
    expect(kickoffLabel("2026-10-25T01:00:00+00:00")).toBe("dom 25/10 · 02:00");
  });
  it("cruza la medianoche y respeta una hora que ya viene en zona de Madrid", () => {
    expect(kickoffLabel("2026-11-08T23:30:00+00:00")).toBe("lun 09/11 · 00:30");
    expect(kickoffLabel("2026-11-08T11:00:00+01:00")).toBe("dom 08/11 · 11:00");
  });
  it("sin zona horaria la hora se toma como local de Madrid (no se desplaza)", () => {
    expect(kickoffLabel("2026-11-08T11:00:00")).toBe("dom 08/11 · 11:00");
    expect(kickoffLabel("2026-11-08T09:05")).toBe("dom 08/11 · 09:05");
  });
  it("solo fecha: sin hora inventada, y el día no se desplaza por la zona", () => {
    expect(formatKickoff("2026-11-08")).toEqual({ date: "dom 08/11", time: null });
    expect(kickoffLabel("2026-11-08")).toBe("dom 08/11");
    expect(kickoffLabel("2026-01-01")).toBe("jue 01/01");
  });
  it("datos ausentes o ilegibles: «Sin fecha» o el texto tal cual, sin lanzar", () => {
    expect(kickoffLabel(null)).toBe("Sin fecha");
    expect(kickoffLabel("")).toBe("Sin fecha");
    expect(kickoffLabel("pronto")).toBe("pronto");
    expect(formatKickoff("2026-13-45")).toBeNull();
    expect(formatKickoff("2026-02-30")).toBeNull();
    expect(formatKickoff("2026-11-08T25:00:00")).toBeNull();
    expect(kickoffLabel("2026-02-30")).toBe("2026-02-30");
  });
});
