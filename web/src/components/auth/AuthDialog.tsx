// web/src/components/auth/AuthDialog.tsx
import { useState } from "react";
import { useAuthStore } from "../../stores/auth";

export function AuthDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const login = useAuthStore((s) => s.login);
  const register = useAuthStore((s) => s.register);
  if (!open) return null;

  async function submit() {
    setError("");
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password);
      onClose();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }

  return (
    <div role="dialog" aria-modal="true">
      <label>邮箱<input aria-label="邮箱" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
      <label>密码<input aria-label="密码" type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
      {error && <p role="alert">{error}</p>}
      <button onClick={submit}>{mode === "login" ? "登录" : "注册"}</button>
      <button onClick={() => setMode(mode === "login" ? "register" : "login")}>
        {mode === "login" ? "没有账号？注册" : "已有账号？登录"}
      </button>
    </div>
  );
}
