import { create } from "zustand";
import { auth, type AuthUser } from "../api/auth";
import { clearSessionToken, getSessionToken } from "../lib/session";
import { isSessionExpiredError, setOnSessionExpired } from "../api/rest";

interface AuthState {
  user: AuthUser | null;
  expired: boolean;                 // 驱动「请重新登录」提示
  login: (e: string, p: string) => Promise<void>;
  register: (e: string, p: string) => Promise<void>;
  logout: () => Promise<void>;
  checkSession: () => Promise<void>;   // App 载入时调
  acknowledgeExpired: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null, expired: false,
  async login(e, p) { const r = await auth.login(e, p); set({ user: r.user, expired: false }); },
  async register(e, p) { const r = await auth.register(e, p); set({ user: r.user, expired: false }); },
  async logout() { try { await auth.logout(); } finally { set({ user: null }); } },
  async checkSession() {
    if (!getSessionToken()) { set({ user: null }); return; }
    try { const me = await auth.me(); set({ user: me.anonymous ? null : (me.user ?? null) }); }
    catch (err) {
      if (isSessionExpiredError(err)) { clearSessionToken(); set({ user: null, expired: true }); }
      else set({ user: null });   // 瞬时错误（断网/5xx/守卫 401）：不摧毁仍有效的会话令牌，待下次重试
    }
  },
  acknowledgeExpired() { set({ expired: false }); },
}));

// 全局注册：任何 REST 遇 401 session_expired → 清会话 + 标记失效（R15）
setOnSessionExpired(() => { clearSessionToken(); useAuthStore.setState({ user: null, expired: true }); });
