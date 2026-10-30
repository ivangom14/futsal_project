import { describe, expect, it } from "vitest";
import {
  appendMessage, deleteConversation, historyFor, loadState, saveState, selectConversation, startConversation, STORAGE_KEY, titleFrom,
  type State,
} from "./conversations";

const empty: State = { conversations: [], activeId: null };
const user = (content: string) => ({ id: content, role: "user" as const, content, createdAt: 1 });

describe("conversaciones", () => {
  it("crea, titula con la primera pregunta y no acumula vacías", () => {
    let s = startConversation(empty);
    expect(s.conversations).toHaveLength(1);
    expect(startConversation(s).conversations).toHaveLength(1);
    s = appendMessage(s, s.activeId!, user("¿Quién lidera?"));
    expect(s.conversations[0].title).toBe("¿Quién lidera?");
    s = appendMessage(s, s.activeId!, user("otra"));
    expect(s.conversations[0].title).toBe("¿Quién lidera?");
    expect(startConversation(s).conversations).toHaveLength(2);
  });

  it("recorta títulos largos", () => {
    expect(titleFrom("x".repeat(100))).toHaveLength(48);
  });

  it("cambia y borra conversaciones manteniendo una activa válida", () => {
    let s = startConversation(empty);
    const a = s.activeId!;
    s = appendMessage(s, a, user("a"));
    s = startConversation(s);
    const b = s.activeId!;
    expect(selectConversation(s, a).activeId).toBe(a);
    expect(selectConversation(s, "nope").activeId).toBe(b);
    s = deleteConversation(s, b);
    expect(s.activeId).toBe(a);
    expect(deleteConversation(s, a)).toEqual(empty);
  });

  it("persiste en localStorage y tolera datos corruptos", () => {
    const base = startConversation(empty);
    const s = appendMessage(base, base.activeId!, user("hola"));
    expect(loadState()).toEqual(empty);
    saveState(s);
    expect(loadState().conversations[0].messages[0].content).toBe("hola");
    localStorage.setItem(STORAGE_KEY, "{no json");
    expect(loadState()).toEqual(empty);
  });

  it("descarta conversaciones con forma inválida en lugar de romper la app", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ conversations: [{ id: "a", title: "x" }, { id: "b", title: "ok", messages: [] }], activeId: "a" }));
    const s = loadState();
    expect(s.conversations.map((c) => c.id)).toEqual(["b"]);
    expect(s.activeId).toBe("b");
  });
});

describe("historyFor", () => {
  const answer = (content: string, over: Record<string, unknown> = {}) => ({
    id: `a-${content}`, role: "assistant" as const, content, createdAt: 2,
    response: { answer: content, guardrail: null, error: null, sources: [], diagnostics: {} as never, ...over },
  });

  it("incluye solo parejas pregunta/respuesta completas y válidas", () => {
    const msgs = [
      user("q1"), answer("r1"),
      user("q2"), { id: "f", role: "assistant" as const, content: "", createdAt: 2, failure: "502" },
      user("q3"), answer("r3", { error: "límite" }),
      user("q4"), answer("r4", { guardrail: "sin_datos_de_tools" }),
      user("q5"), answer("r5", { guardrail: "aclaracion_ambito" }),
      user("q6"),
    ];
    expect(historyFor(msgs).map((h) => h.content)).toEqual(["q1", "r1", "q5", "r5"]);
  });

  it("limita el número de turnos enviados", () => {
    const msgs = Array.from({ length: 50 }, (_, i) => [user(`q${i}`), answer(`r${i}`)]).flat();
    const h = historyFor(msgs);
    expect(h).toHaveLength(40);
    expect(h[h.length - 1].content).toBe("r49");
  });
});
