/** Escudos oficiales en la UI: se piden una vez a /chat/crests; con imagen, <img> enlazada; sin ella o si falla, monograma. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CrestProvider, crestKey } from "./crests";
import TeamCrest from "./ui/TeamCrest";

const URL_DOSA = "https://appweb.rffm.es/pnfg/pimg/Clubes/dosa.png";
const serve = (body: unknown) => {
  const fetchMock = vi.fn((_url: string) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 })));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};
afterEach(() => vi.unstubAllGlobals());

describe("escudos oficiales", () => {
  it("con CRESTS_MODE=link pinta la imagen de la RFFM sin enviar el referer y la busca sin tildes ni espacios dobles", async () => {
    const fetchMock = serve({ mode: "link", crests: { "C.D. DOSA": URL_DOSA } });
    render(<CrestProvider><TeamCrest name="c.d.  dosa" /><TeamCrest name="FS ALCALA" /></CrestProvider>);
    await waitFor(() => expect(document.querySelector("img.team-crest")).not.toBeNull());
    const img = document.querySelector("img.team-crest")!;
    expect(img).toHaveAttribute("src", URL_DOSA);
    expect(img).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(img).toHaveAttribute("alt", "");
    expect(document.querySelectorAll("svg.team-crest")).toHaveLength(1); // ALCALA sin escudo: monograma
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/chat\/crests$/);
  });

  it("si la imagen no carga, vuelve al monograma", async () => {
    serve({ mode: "link", crests: { "C.D. DOSA": URL_DOSA } });
    render(<CrestProvider><TeamCrest name="C.D. DOSA" /></CrestProvider>);
    await waitFor(() => expect(document.querySelector("img.team-crest")).not.toBeNull());
    fireEvent.error(document.querySelector("img.team-crest")!);
    expect(document.querySelector("img.team-crest")).toBeNull();
    expect(screen.getByTestId("team-crest").tagName.toLowerCase()).toBe("svg");
  });

  it("apagado, respuesta rara o error de red: siempre monogramas", async () => {
    for (const body of [{ mode: "off", crests: {} }, { answer: "no es un mapa" }, { mode: "link", crests: { X: "javascript:alert(1)" } }]) {
      serve(body);
      const { unmount } = render(<CrestProvider><TeamCrest name="X" /></CrestProvider>);
      await new Promise((r) => setTimeout(r, 0));
      expect(document.querySelector("img")).toBeNull();
      unmount();
    }
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("red"))));
    render(<CrestProvider><TeamCrest name="X" /></CrestProvider>);
    await new Promise((r) => setTimeout(r, 0));
    expect(document.querySelector("img")).toBeNull();
  });

  it("crestKey normaliza mayúsculas, tildes y espacios", () => {
    expect(crestKey("  Atlético  Álamo ")).toBe("ATLETICO ALAMO");
  });
});
