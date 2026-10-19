import { useCallback, useEffect, useRef, useState } from "react";
import { rest } from "../api/rest";
import { getKeys, hasDashScopeKey } from "../lib/keys";

/**
 * 认证门禁（评审 R8 拆分自 App.tsx 的 Workspace）：无 key 只可浏览不可发 + 401 就地弹层。
 *
 * - 无 key 拦截：本地百炼 key 优先（免查）；否则 GET /settings/status 定夺。查询失败**不锁**——
 *   旧后端没有该端点，锁死会让已配 key 的部署不可用（后端 403 llm_key_required 兜底）。
 * - 401 弹层：保存了新 App Token 时 `authRetry +1`，调用方把它传给 useProjectBootstrap
 *   以重跑快照/历史（免整页刷新）；补填了百炼 key 则就地解锁。
 * - bfcache：回首页保存 key 后「后退」回到本页时页面从缓存恢复（React 不重挂载、effect
 *   不重跑），persisted 的 pageshow 事件里再查一次，避免已保存的 key 被旧状态误判为未配置。
 */
export function useAuthGate(projectId: string) {
  const [composerLocked, setComposerLocked] = useState(false);
  const [keyDialogOpen, setKeyDialogOpen] = useState(false);
  const [authRetry, setAuthRetry] = useState(0);   // 401 弹层保存新令牌后 +1 → 重拉数据
  const tokenBeforeDialog = useRef("");             // 弹层打开前的令牌快照（关闭时比对）

  useEffect(() => {
    const onShow = (e: PageTransitionEvent) => {
      if (!e.persisted) return;
      if (hasDashScopeKey()) {
        setComposerLocked(false);
        return;
      }
      rest
        .settingsStatus()
        .then((s) => setComposerLocked(!s.dashscope_configured))
        .catch(() => {});
    };
    window.addEventListener("pageshow", onShow);
    return () => window.removeEventListener("pageshow", onShow);
  }, []);

  // 无 key 拦截：本地 key 优先（免查）；否则等服务端结论。查询失败不锁（后端 403 兜底），
  // 避免旧后端（无 /settings/status）把已配 key 的部署锁死。
  useEffect(() => {
    if (hasDashScopeKey()) {
      setComposerLocked(false);
      return;
    }
    let alive = true;
    rest
      .settingsStatus()
      .then((s) => {
        if (alive) setComposerLocked(!s.dashscope_configured);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [projectId]);

  const openKeyDialog = useCallback(() => {
    tokenBeforeDialog.current = getKeys().appToken;
    setKeyDialogOpen(true);
  }, []);

  const closeKeyDialog = useCallback(() => {
    setKeyDialogOpen(false);
    if (getKeys().appToken !== tokenBeforeDialog.current) setAuthRetry((n) => n + 1);
    if (hasDashScopeKey()) setComposerLocked(false);
  }, []);

  return { composerLocked, keyDialogOpen, openKeyDialog, closeKeyDialog, authRetry };
}
