// web/tests/keys.spec.ts
import { beforeEach, describe, expect, it, vi } from "vitest";
import { authHeaders, clearKeys, getKeys, saveKeys } from "../src/lib/keys";
import { streamTurnEvents } from "../src/api/sse";

describe("keys", () => {
  beforeEach(() => localStorage.clear());

  it("saveKeys 三参写入并可读回（plan/amap 未传不动，读回空）", () => {
    saveKeys("sk-a", "fly-b", "tok-c");
    expect(getKeys()).toEqual({ dashscope: "sk-a", flyai: "fly-b", appToken: "tok-c", plan: "", amap: "" });
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

  it("clearKeys 清全部（含套餐 plan 与高德 amap）", () => {
    saveKeys("sk-a", "fly-b", "tok-c", "coding", "ak-x");
    clearKeys();
    expect(getKeys()).toEqual({ dashscope: "", flyai: "", appToken: "", plan: "", amap: "" });
  });

  it("saveKeys 第五参写 amap 并注入 X-Amap-Key 头；未传不动已存值", () => {
    saveKeys("sk-a", "fly-b", "tok-c", "", "ak-1");
    expect(getKeys().amap).toBe("ak-1");
    expect(authHeaders()["X-Amap-Key"]).toBe("ak-1");
    saveKeys("sk-d", ""); // amap 未传（undefined）不动已存值
    expect(getKeys().amap).toBe("ak-1");
  });

  it("streamTurnEvents 携带 App Token 头", async () => {
    saveKeys("", "", "secret-token");
    const inits: Array<RequestInit | undefined> = [];
    // N14：非终态 EOF 会按断线恢复重连，故桩流需吐一个终态帧（turn_done）才会正常关闭；
    // 本用例只关心首连请求头，终态帧让 streamTurnEvents 走完一次连接即返回。
    const encoder = new TextEncoder();
    const chunks = [encoder.encode('id: 1\nevent: turn_done\ndata: {"turn_id":"t","seq":1,"reply_id":"t","usage":{}}\n\n')];
    const resp = {
      ok: true,
      body: {
        getReader: () => ({
          read: async () => chunks.length
            ? { value: chunks.shift() as Uint8Array, done: false }
            : { value: undefined, done: true },
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
      "X-Owner-Token": expect.stringMatching(/^[0-9a-f]{32}$/),
    });
  });
});
