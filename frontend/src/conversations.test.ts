import { describe, expect, it } from "vitest";
import {
  appendMessage, deleteConversation, loadState, saveState, selectConversation, startConversation, STORAGE_KEY, titleFrom,
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
