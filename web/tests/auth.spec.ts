import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError, isAppTokenError, isSessionExpiredError, notifySessionExpiredIfAny, rest,
} from "../src/api/rest";
import { auth } from "../src/api/auth";
import {
  clearSessionToken, getAuthEpoch, getSessionToken, setSessionToken,
} from "../src/lib/session";
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

  it("T03：迟到的旧身份 401 不清新登录会话；当前身份 401 仍正常退出（真实 req 分发）", async () => {
    const ALICE = { user_id: "u-alice", email: "alice@example.test" };
    const BOB = { user_id: "u-bob", email: "bob@example.test" };
    const EXPIRED = JSON.stringify({ detail: { code: "session_expired" } });
    setSessionToken("sess_alice");
    useAuthStore.setState({ user: ALICE, expired: false });

    let release!: (r: Response) => void;
    const pending = new Promise<Response>((resolve) => { release = resolve; });
    vi.stubGlobal("fetch", vi.fn(() => pending));
    const late = rest.getProject("p1").catch((e: unknown) => e);

    // Alice 退出、Bob 登录（真实令牌 + store 状态），再把 Alice 的旧 401 交付
    clearSessionToken();
    setSessionToken("sess_bob");
    useAuthStore.setState({ user: BOB, expired: false });
    release({
      ok: false, status: 401, text: async () => EXPIRED,
    } as Response);

    const err = await late;
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).staleAuth).toBe(true);
    expect(getSessionToken()).toBe("sess_bob");
    expect(useAuthStore.getState().user).toEqual(BOB);
    expect(useAuthStore.getState().expired).toBe(false);

    // 当前 Bob 请求真正失效：仍正常退出
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: false, status: 401, text: async () => EXPIRED,
    })));
    await expect(rest.getProject("p1")).rejects.toBeInstanceOf(ApiError);
    expect(getSessionToken()).toBe("");
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().expired).toBe(true);
  });

  it("T03：notifySessionExpiredIfAny 按发起时代次判定（SSE/下载共用同一规则）", () => {
    const EXPIRED = new ApiError(401, JSON.stringify({ detail: { code: "session_expired" } }));
    setSessionToken("sess_bob");
    useAuthStore.setState({ user: { user_id: "u-bob", email: "b@example.test" }, expired: false });
    const before = getAuthEpoch();
    setSessionToken("sess_carol");          // 新会话：旧响应到达
    notifySessionExpiredIfAny(EXPIRED, before);
    expect(getSessionToken()).toBe("sess_carol");
    expect(useAuthStore.getState().user).not.toBeNull();
    // 当前代次的同体 401 仍触发全局清理
    notifySessionExpiredIfAny(EXPIRED, getAuthEpoch());
    expect(getSessionToken()).toBe("");
    expect(useAuthStore.getState().expired).toBe(true);
  });

  it("T03：同一会话的并发 401 不被误判为旧身份", async () => {
    const EXPIRED = JSON.stringify({ detail: { code: "session_expired" } });
    setSessionToken("sess_same");
    useAuthStore.setState({ user: { user_id: "u-same", email: "s@example.test" }, expired: false });
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: false, status: 401, text: async () => EXPIRED,
    })));
    const [a, b] = await Promise.all([
      rest.getProject("p1").catch((e: unknown) => e),
      rest.getProject("p1").catch((e: unknown) => e),
    ]);
    expect((a as ApiError).staleAuth).toBe(false);
    expect((b as ApiError).staleAuth).toBe(false);   // 第二个 401 同样属于刚失效的当前会话
    expect(getSessionToken()).toBe("");
    expect(useAuthStore.getState().expired).toBe(true);
  });
});
