// web/tests/keys.spec.ts
import { beforeEach, describe, expect, it, vi } from "vitest";
import { authHeaders, clearKeys, getKeys, saveKeys } from "../src/lib/keys";
import { streamTurnEvents } from "../src/api/sse";

describe("keys", () => {
  beforeEach(() => localStorage.clear());

  it("saveKeys 三参写入并可读回", () => {
    saveKeys("sk-a", "fly-b", "tok-c");
    expect(getKeys()).toEqual({ dashscope: "sk-a", flyai: "fly-b", appToken: "tok-c" });
  });

  it("两参调用不动已存令牌（向后兼容）", () => {
    saveKeys("sk-a", "", "tok-c");
    saveKeys("sk-d", "");
    expect(getKeys().appToken).toBe("tok-c");
    expect(getKeys().dashscope).toBe("sk-d");
  });

  it("authHeaders 注入三个头；未存不带 X-App-Token", () => {
    saveKeys("sk-a", "", "tok-c");
    expect(authHeaders()).toEqual({ "X-DashScope-Key": "sk-a", "X-App-Token": "tok-c" });
    clearKeys();
    expect(authHeaders()).toEqual({});
  });

  it("clearKeys 清三项", () => {
    saveKeys("sk-a", "fly-b", "tok-c");
    clearKeys();
    expect(getKeys()).toEqual({ dashscope: "", flyai: "", appToken: "" });
  });

  it("streamTurnEvents 携带 App Token 头", async () => {
    saveKeys("", "", "secret-token");
    const inits: Array<RequestInit | undefined> = [];
    const resp = {
      ok: true,
      body: {
        getReader: () => ({
          read: async () => ({ value: undefined, done: true }),
          cancel: async () => {},
        }),
      },
    };
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      inits.push(init ?? {});
      return resp as unknown as Response;
    }));
    await streamTurnEvents("p1", "0123456789abcdef", { onEvent: () => {} });
    expect(inits[0]?.headers).toMatchObject({
      Accept: "text/event-stream",
      "X-App-Token": "secret-token",
    });
  });
});
