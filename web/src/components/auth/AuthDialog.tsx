// web/src/components/auth/AuthDialog.tsx
import { useEffect, useState } from "react";
import { ApiError } from "../../api/rest";
import { useAuthStore } from "../../stores/auth";

/** 认证失败 → 用户可读文案：优先取后端 detail（字符串或 {code,message} 形状）内层
 *  message；非 JSON 体/未知形状回落 HTTP 状态话术，绝不把响应体原文甩用户脸上。 */
function humanError(e: unknown): string {
  if (e instanceof ApiError) {
    try {
      const d = JSON.parse(e.message)?.detail;
      if (typeof d === "string") return d;
      if (d && typeof d.message === "string") return d.message;
    } catch { /* 非 JSON 体走默认 */ }
    return `请求失败（HTTP ${e.status}），请稍后重试`;
  }
  return e instanceof Error ? e.message : String(e);
}

export function AuthDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);   // submit 并发守卫：请求进行中禁重复提交
  const login = useAuthStore((s) => s.login);
  const register = useAuthStore((s) => s.register);
  // 重开弹窗即清上次失败文案，陈旧错误不跨会话残留
  useEffect(() => { if (open) setError(""); }, [open]);
  if (!open) return null;

  async function submit() {
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password);
      onClose();
    } catch (e) { setError(humanError(e)); }
    finally { setBusy(false); }
  }

  return (
    <div role="dialog" aria-modal="true" onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
      <label>邮箱<input aria-label="邮箱" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
      <label>密码<input aria-label="密码" type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
      {error && <p role="alert">{error}</p>}
      <button onClick={submit} disabled={busy}>{mode === "login" ? "登录" : "注册"}</button>
      <button onClick={() => { setError(""); setMode(mode === "login" ? "register" : "login"); }}>
        {mode === "login" ? "没有账号？注册" : "已有账号？登录"}
      </button>
    </div>
  );
}
