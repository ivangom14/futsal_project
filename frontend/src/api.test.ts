import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, askAgent, forgetConversation, getHealth } from "./api";
import contract from "../../tests/fixtures/chat_response.json";

const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));

afterEach(() => vi.unstubAllGlobals());

describe("cliente HTTP", () => {
  it("envía la pregunta como JSON a POST /chat y devuelve el contrato", async () => {
    const fetchMock = vi.fn(() => json(contract));
    vi.stubGlobal("fetch", fetchMock);
    const res = await askAgent("¿Cuánto dura cada mitad?", "conv-1", [{ role: "user", content: "a" }, { role: "assistant", content: "b" }]);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/chat$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      question: "¿Cuánto dura cada mitad?",
      conversation_id: "conv-1",
      history: [{ role: "user", content: "a" }, { role: "assistant", content: "b" }],
    });
    expect(res.diagnostics.history_turns).toBe(0);
    expect(res.sources[0].kind).toBe("rag");
  });

  it("convierte errores HTTP en ApiError con el detalle del backend", async () => {
    vi.stubGlobal("fetch", () => json({ detail: "error del agente: cuota agotada" }, 502));
    await expect(askAgent("x", "c")).rejects.toMatchObject({ status: 502, message: "error del agente: cuota agotada" });
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
  });
});
