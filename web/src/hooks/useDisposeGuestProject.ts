import { useEffect } from "react";
import { authHeaders } from "../lib/keys";
import { getSessionToken, ownerHeaders } from "../lib/session";

/**
 * useDisposeGuestProject — 匿名（未注册）会话离开工作台时清除项目记录。
 *
 * 业务前提（参赛演示形态）：App Token 是演示用的，未登录也能完整使用项目功能，
 * 但**匿名记录不留存**——只有注册账号名下的对话才会长期保存。首页对未登录用户
 * 本来就不展示项目列表，因此"匿名项目不跨会话"与现有信息架构一致。
 *
 * 删除时机与豁免（用户裁决）：
 * - `beforeunload`：弹浏览器原生确认框（文案由浏览器固定，无法定制）；
 * - `pagehide`：真正离开时才发 `DELETE /projects/{id}`（`keepalive` 让请求在文档
 *   销毁后仍送达）；
 * - **刷新豁免**：F5 / Ctrl+R / Cmd+R 时置 `reloading` 标记，既不弹窗也不删除——
 *   否则演示现场一次误刷新就丢光对话；
 * - **bfcache 豁免**：`pagehide.persisted === true` 时跳过，用户可能按返回键回来；
 * - **应用内离开**：返回首页（LeftRail 按钮）与浏览器后退（popstate）都是 SPA 导航，
 *   不触发 pagehide，这里补两个口子；/p/<id> 内部的页间跳转（如方案页）不删。
 *
 * 注册/登录成功后 `enabled` 变 false，监听解绑：此时后端 `auth.py::_claim` 已把
 * `anon:<token>` 项目 CAS 迁到 `user:<id>`，绝不能再删。
 *
 * 已知缺口：浏览器崩溃/强杀进程不触发 pagehide，匿名项目会残留。强保证需要后端按
 * `anon:` 前缀 + 时间做清理任务，不在本次范围。
 */
export function useDisposeGuestProject(projectId: string, enabled: boolean) {
  useEffect(() => {
    if (!enabled || !projectId) return;
    let reloading = false;

    // 手动刷新豁免（工具栏刷新按钮拦不住，只能接受）
    const onKeyDown = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase();
      if (key === "f5" || ((e.ctrlKey || e.metaKey) && key === "r")) reloading = true;
    };
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (reloading) return;
      e.preventDefault();
      e.returnValue = "";   // 触发浏览器原生确认（自定义文案不会显示）
      return "";
    };
    const onPageHide = (e: PageTransitionEvent) => {
      if (reloading || e.persisted) return;
      // 双保险：卸载瞬间再确认仍是匿名。会话令牌存在就不删——否则慢网下
      // checkSession 未回（user 恒为 null）时关闭页面会删掉登录用户的项目。
      if (getSessionToken()) return;
      disposeGuestProject(projectId);
    };
    // 浏览器后退退出工作台：popstate 时地址已更新，看是否还在本项目路由下
    const onPopState = () => {
      if (reloading || getSessionToken()) return;
      const home = `/p/${projectId}`;
      const path = window.location.pathname;
      // 严格匹配 /p/<id> 或 /p/<id>/...（裸 startsWith 会把 p10 当成 p1）
      if (path !== home && !path.startsWith(`${home}/`)) disposeGuestProject(projectId);
    };

    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("beforeunload", onBeforeUnload);
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("popstate", onPopState);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("beforeunload", onBeforeUnload);
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("popstate", onPopState);
    };
  }, [projectId, enabled]);
}

/**
 * 立即删除匿名项目（应用内离开用：返回首页等 SPA 导航不触发 pagehide）。
 *
 * 走与 REST 层相同的头（会话令牌/匿名令牌 + App Token），路径与 `rest.deleteProject`
 * 同源（后端 `DELETE /projects/{id}`：取消活动回合 → 清 repo → 清 turns 残留）。
 * 卸载期失败无处补救，静默；应用内调用方也不阻塞导航（keepalive 保证送达）。
 */
export function disposeGuestProject(projectId: string): void {
  if (!projectId) return;
  if (getSessionToken()) return;   // 登录会话不得走匿名清除路径（调用方已拦，这里再兜一层）
  try {
    void fetch(`/projects/${encodeURIComponent(projectId)}`, {
      method: "DELETE",
      keepalive: true,
      headers: { ...authHeaders(), ...ownerHeaders() },
    });
  } catch {
    // 页面正在销毁：既无 UI 也无重试窗口
  }
}
