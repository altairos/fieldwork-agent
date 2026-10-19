import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, isAppTokenError, isSessionExpiredError } from "../src/api/rest";
import { auth } from "../src/api/auth";
import { getSessionToken, setSessionToken } from "../src/lib/session";
import { useAuthStore } from "../src/stores/auth";

describe("auth client + session expiry", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it("isSessionExpiredError 只认 401+session_expired", () => {
    expect(isSessionExpiredError(new ApiError(401, JSON.stringify({ detail: { code: "session_expired" } })))).toBe(true);
    expect(isSessionExpiredError(new ApiError(401, JSON.stringify({ detail: { code: "app_token_required" } })))).toBe(false);
    expect(isSessionExpiredError(new ApiError(404, "{}"))).toBe(false);
  });

  it("login 存会话令牌", async () => {
    const mock = vi.fn().mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ session_token: "sess_z", user: { user_id: "u1", email: "a@b.com" }, claim: { claimed: 0, skipped: 0 } }),
    });
    vi.stubGlobal("fetch", mock);
    await auth.login("a@b.com", "s3cret-pass");
    expect(getSessionToken()).toBe("sess_z");
  });

  it("isAppTokenError 不认 detail 形状（session_expired 不误导填令牌）", () => {
    expect(isAppTokenError(new ApiError(401, JSON.stringify({ detail: { code: "session_expired" } })))).toBe(false);
    expect(isAppTokenError(new ApiError(401, JSON.stringify({ code: "app_token_required" })))).toBe(true);
    expect(isSessionExpiredError(new ApiError(401, "Unauthorized"))).toBe(false);   // 非 JSON 体保守 false
  });

  it("checkSession 瞬时错误不清会话令牌，401 session_expired 才清", async () => {
    setSessionToken("sess_keep");
    // 瞬时错误：fetch 网络拒绝 → 令牌保留
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
    await useAuthStore.getState().checkSession();
    expect(getSessionToken()).toBe("sess_keep");
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().expired).toBe(false);
    // 真会话失效：401 detail 体 → 清令牌 + 标记
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 401,
      text: async () => JSON.stringify({ detail: { code: "session_expired" } }),
    }));
    await useAuthStore.getState().checkSession();
    expect(getSessionToken()).toBe("");
    expect(useAuthStore.getState().expired).toBe(true);
    vi.unstubAllGlobals();
  });
});
