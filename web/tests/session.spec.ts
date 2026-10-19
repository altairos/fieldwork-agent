// web/tests/session.spec.ts —— 匿名归属令牌：懒生成/稳定/隐私模式内存兜底；会话令牌登录态优先
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearSessionToken,
  getOwnerToken,
  getSessionToken,
  ownerHeaders,
  randomToken,
  setSessionToken,
} from "../src/lib/session";

describe("session owner token", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("getOwnerToken 懒生成 32 位 hex 并持久化（两次同值）", () => {
    const a = getOwnerToken();
    expect(a).toMatch(/^[0-9a-f]{32}$/);
    expect(getOwnerToken()).toBe(a);
    expect(localStorage.getItem("fieldwork.owner_token")).toBe(a);
  });

  it("ownerHeaders 恒携带 X-Owner-Token 且与 getOwnerToken 一致", () => {
    expect(ownerHeaders()).toEqual({ "X-Owner-Token": getOwnerToken() });
  });

  it("randomToken 降级路径（无 crypto.randomUUID 时）仍 32 位 hex", () => {
    expect(randomToken()).toMatch(/^[0-9a-f]{32}$/);
  });

  it("localStorage 抛错时内存兜底：同会话内 token 稳定", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    const a = getOwnerToken();
    expect(a).toMatch(/^[0-9a-f]{32}$/);
    expect(getOwnerToken()).toBe(a);   // 内存 Map 兜底，不因存储被禁而漂移
  });

  it("setSessionToken 后 ownerHeaders 优先发会话令牌", () => {
    setSessionToken("sess_abc");
    expect(getSessionToken()).toBe("sess_abc");
    expect(ownerHeaders()).toEqual({ "X-Owner-Token": "sess_abc" });
  });

  it("clearSessionToken 后 ownerHeaders 回落匿名令牌", () => {
    setSessionToken("sess_abc");
    clearSessionToken();
    expect(getSessionToken()).toBe("");
    expect(ownerHeaders()).toEqual({ "X-Owner-Token": getOwnerToken() });
  });
});
