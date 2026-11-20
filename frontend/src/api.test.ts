import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, askAgent, forgetConversation, getCrests, getHealth, sendFeedback } from "./api";
import contract from "../../tests/fixtures/chat_response.json";

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));

afterEach(() => vi.unstubAllGlobals());

describe("cliente HTTP", () => {
  it("envía la pregunta como JSON a POST /chat y devuelve el contrato", async () => {
    const fetchMock = vi.fn(() => json(contract));
    vi.stubGlobal("fetch", fetchMock);
    const res = await askAgent("¿Cuánto dura cada mitad?", "conv-1", "futsal", [{ role: "user", content: "a" }, { role: "assistant", content: "b" }]);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/chat$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      question: "¿Cuánto dura cada mitad?",
      conversation_id: "conv-1",
      sport: "futsal",
      history: [{ role: "user", content: "a" }, { role: "assistant", content: "b" }],
      focus: {},
    });
    expect(res.diagnostics.history_turns).toBe(0);
    expect(res.sources[0].kind).toBe("document");
  });

  it("convierte errores HTTP en ApiError con el detalle del backend", async () => {
    vi.stubGlobal("fetch", () => json({ detail: "Espera un poco.", code: "rate_limit", technical: "LLM HTTP 429" }, 429));
    await expect(askAgent("x", "c", "futsal")).rejects.toMatchObject({ status: 429, message: "Espera un poco.", code: "rate_limit", technical: "LLM HTTP 429" });
  });

  it("pide al servidor olvidar una conversación con DELETE", async () => {
    const fetchMock = vi.fn(() => json({ deleted: true }));
    vi.stubGlobal("fetch", fetchMock);
    await forgetConversation("conv-1");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/chat\/conversations\/conv-1$/);
    expect(init.method).toBe("DELETE");
  });

  it("distingue un fallo de red (status 0)", async () => {
    vi.stubGlobal("fetch", () => Promise.reject(new TypeError("fail")));
    const err = await getHealth().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.code).toBe("connection");
    expect(err.message).toMatch(/No he podido conectar/);
  });

  it.each(["futsal", "football11"] as const)("askAgent, feedback y escudos llevan el deporte (%s)", async (sport) => {
    const fetchMock = vi.fn(() => json({ saved: true, mode: "off", crests: {}, ...contract }));
    vi.stubGlobal("fetch", fetchMock);
    await askAgent("q", "c1", sport, [], { team: "A" });
    await sendFeedback("c1", "m1", null, { rating: "up" }, sport);
    await getCrests(sport);
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(JSON.parse(calls[0][1].body as string)).toMatchObject({ sport, conversation_id: "c1", focus: { team: "A" } });
    expect(JSON.parse(calls[1][1].body as string)).toMatchObject({ sport, message_id: "m1" });
    expect(calls[2][0]).toMatch(new RegExp(`/chat/crests\\?sport=${sport}$`));
  });

  it("getHealth y forgetConversation no llevan deporte", async () => {
    const fetchMock = vi.fn(() => json({ deleted: true, status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);
    await getHealth();
    await forgetConversation("c1");
    for (const [url, init] of fetchMock.mock.calls as unknown as [string, RequestInit | undefined][]) {
      expect(url).not.toMatch(/sport/);
      expect(init?.body ?? "").not.toMatch(/sport/);
    }
  });
});
