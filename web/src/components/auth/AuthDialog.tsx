// web/src/components/auth/AuthDialog.tsx
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/rest";
import { clearRememberedEmail, readRememberedEmail, writeRememberedEmail } from "../../lib/session";
import { useAuthStore } from "../../stores/auth";
import { ApiKeySection } from "../settings/ApiKeyCard";
import { IconCaret, IconClose, IconEye, IconEyeOff } from "../ui/icons";

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

/**
 * AuthDialog — 登录/注册弹层（design-board ⑤ 视觉稿）。g1 磨砂玻璃面板：
 * 本分支尚无 .fw-glass（毛玻璃二期在 main），等价自包含写法
 * `bg-card/80 + backdrop-blur-[8px]`，合流后一行替换为 .fw-glass.g1；
 * 玻璃仅用于悬浮层，输入壳等实底部件不套玻璃。
 * 功能语义零回退：mode 切换、humanError 错误话术、busy 并发守卫、Esc 关闭、
 * 重开清错误。增强：密码显隐、记住邮箱（仅登录模式，永不存密码）、Enter 提交
 * （form 包裹，其余钮显式 type="button" 收窄回车误触面）、打开聚焦邮箱、
 * busy「登录中…/注册中…」、底部「高级 · API 密钥」折叠区（收起不挂载
 * ApiKeySection——既有用例查询空间隔离 + 面板高度可控；initialKeysOpen
 * 供 401 分流自动展开）。三通道关闭：右上钮 / Esc / 点击遮罩。
 * 动效用 tokens.css 全局 keyframes（fade-in/sheet-in），reduced-motion 全关。
 */
export function AuthDialog({ open, onClose, initialKeysOpen = false }: {
  open: boolean;
  onClose: () => void;
  initialKeysOpen?: boolean;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);   // submit 并发守卫：请求进行中禁重复提交
  const [showPwd, setShowPwd] = useState(false);
  const [remember, setRemember] = useState(false);   // 勾选是用户主动动作，默认不勾
  const [keysOpen, setKeysOpen] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);
  const login = useAuthStore((s) => s.login);
  const register = useAuthStore((s) => s.register);
  // 重开弹窗：清上次失败文案、显隐复位、密码恒空、折叠区按 initialKeysOpen 定、
  // 预填已记住的邮箱、聚焦邮箱——陈旧错误与残留输入不跨会话带过来
  useEffect(() => {
    if (!open) return;
    setError("");
    setShowPwd(false);
    setPassword("");
    setRemember(false);
    setKeysOpen(initialKeysOpen);
    setEmail(readRememberedEmail());
    emailRef.current?.focus();
  }, [open, initialKeysOpen]);
  if (!open) return null;

  async function submit() {
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      if (mode === "login") {
        await login(email, password);
        // 存储只随登录路径动：勾选写入 / 不勾清除（旧值不残留）；注册路径不碰
        if (remember) writeRememberedEmail(email);
        else clearRememberedEmail();
      } else {
        await register(email, password);
      }
      onClose();
    } catch (e) { setError(humanError(e)); }
    finally { setBusy(false); }
  }

  return (
    <div
      role="presentation"
      onClick={onClose}
      className="fixed inset-0 z-50 grid place-items-center px-4 py-8"
    >
      <div className="absolute inset-0 bg-ink/40 fade-in" aria-hidden="true" />
      <form
        role="dialog"
        aria-modal="true"
        aria-label="登录或注册"
        onSubmit={(e) => { e.preventDefault(); submit(); }}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
        className="relative hairline rounded-xl shadow-pop sheet-in bg-card/80 backdrop-blur-[8px] w-full max-w-[400px] max-h-full overflow-y-auto scroll"
      >
        <div className="flex items-center gap-2.5 px-5 pt-5 pb-4">
          <img src="/fieldwork-logo-full.svg" alt="" aria-hidden="true" className="w-7 h-7 shrink-0" />
          <div className="flex-1 min-w-0 leading-tight">
            <div className="font-serif text-[16.5px] font-semibold">
              {mode === "login" ? "欢迎回来" : "创建账号"}
            </div>
            <div className="text-[11px] text-inkfaint mt-0.5">
              {mode === "login" ? "登录以继续你的田野项目" : "注册一个田野罗盘账号"}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="w-7 h-7 grid place-items-center rounded-md text-inkfaint hover:text-ink hover:bg-cardsunk transition cursor-pointer shrink-0"
          >
            <IconClose className="w-4 h-4" />
          </button>
        </div>
        <div className="mx-5 border-t border-hairline" />
        <div className="px-5 py-4">
          <label className="block mb-3.5">
            <span className="block text-[12px] text-inksoft mb-1.5">邮箱</span>
            <span className="flex items-center hairline rounded-md bg-paper/60 h-10 transition focus-within:border-cinnabar/50">
              <input
                ref={emailRef}
                aria-label="邮箱"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="flex-1 min-w-0 h-full bg-transparent px-3 text-[13px] text-ink outline-none"
              />
            </span>
          </label>
          <label className="block mb-3.5">
            <span className="block text-[12px] text-inksoft mb-1.5">密码</span>
            <span className="flex items-center hairline rounded-md bg-paper/60 h-10 transition focus-within:border-cinnabar/50">
              <input
                aria-label="密码"
                type={showPwd ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="flex-1 min-w-0 h-full bg-transparent px-3 text-[13px] text-ink outline-none"
              />
              <button
                type="button"
                onClick={() => setShowPwd(!showPwd)}
                aria-label={showPwd ? "隐藏密码" : "显示密码"}
                aria-pressed={showPwd}
                className="w-9 h-full grid place-items-center text-inkfaint hover:text-inksoft transition cursor-pointer shrink-0"
              >
                {showPwd
                  ? <IconEyeOff className="w-[18px] h-[18px]" />
                  : <IconEye className="w-[18px] h-[18px]" />}
              </button>
            </span>
          </label>
          {error && <p role="alert" className="text-danger text-[12.5px] mb-3.5">{error}</p>}
          {mode === "login" && (
            <label className="flex items-center gap-2 mb-4 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                className="w-3.5 h-3.5"
                style={{ accentColor: "#A8412E" }}
              />
              <span className="text-[12px] text-inksoft">记住邮箱</span>
            </label>
          )}
          <button
            type="submit"
            disabled={busy}
            className="w-full h-10 rounded-md bg-cinnabar text-ontint text-[13.5px] font-medium hover:bg-tintpress transition disabled:opacity-50 cursor-pointer"
          >
            {busy ? (mode === "login" ? "登录中…" : "注册中…") : (mode === "login" ? "登录" : "注册")}
          </button>
          <div className="text-center mt-3.5">
            <button
              type="button"
              onClick={() => { setError(""); setMode(mode === "login" ? "register" : "login"); }}
              className="text-[12.5px] text-indigo hover:underline cursor-pointer"
            >
              {mode === "login" ? "没有账号？注册" : "已有账号？登录"}
            </button>
          </div>
        </div>
        <div className="mx-5 border-t border-hairline" />
        <div className="px-5 py-3">
          <button
            type="button"
            onClick={() => setKeysOpen(!keysOpen)}
            aria-expanded={keysOpen}
            className="flex items-center gap-1.5 text-[12px] text-inksoft hover:text-ink transition cursor-pointer"
          >
            <IconCaret className={`w-3.5 h-3.5 transition-transform ${keysOpen ? "rotate-90" : ""}`} />
            高级 · API 密钥
          </button>
        </div>
        {keysOpen && (
          <div className="px-5 pb-5">
            <ApiKeySection />
          </div>
        )}
      </form>
    </div>
  );
}
