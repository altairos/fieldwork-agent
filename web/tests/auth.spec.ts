import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, isSessionExpiredError } from "../src/api/rest";
import { auth } from "../src/api/auth";
import { getSessionToken } from "../src/lib/session";

describe("auth client + session expiry", () => {
  beforeEach(() => localStorage.clear());

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
    vi.unstubAllGlobals();
  });
});
