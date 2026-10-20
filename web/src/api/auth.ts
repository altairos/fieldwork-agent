import { authHeaders } from "../lib/keys";
import { ApiError } from "./rest";
import { clearSessionToken, getOwnerToken, ownerHeaders, setSessionToken } from "../lib/session";

export interface AuthUser { user_id: string; email: string; created_at?: string; }
export interface AuthResult { session_token: string; user: AuthUser; claim: { claimed: number; skipped: number }; }

async function authReq<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...ownerHeaders(), ...init?.headers },
  });
  if (!resp.ok) throw new ApiError(resp.status, await resp.text().catch(() => resp.statusText));
  return resp.status === 204 ? (undefined as T) : (await resp.json() as T);
}

export const auth = {
  async register(email: string, password: string): Promise<AuthResult> {
    const r = await authReq<AuthResult>("/auth/register", { method: "POST",
      body: JSON.stringify({ email, password, anon_token: getOwnerToken() }) });
    setSessionToken(r.session_token); return r;
  },
  async login(email: string, password: string): Promise<AuthResult> {
    const r = await authReq<AuthResult>("/auth/login", { method: "POST",
      body: JSON.stringify({ email, password, anon_token: getOwnerToken() }) });
    setSessionToken(r.session_token); return r;
  },
  // 本地清理不因网络失败短路：无论 POST 成败都清会话令牌
  async logout(): Promise<void> { try { await authReq<void>("/auth/logout", { method: "POST" }); } finally { clearSessionToken(); } },
  me: () => authReq<{ anonymous: boolean; user?: AuthUser }>("/auth/me"),
};
