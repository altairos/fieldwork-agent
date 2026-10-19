import { useEffect, useRef } from "react";
import { isAppTokenError, rest } from "../api/rest";
import { useConversationStore } from "../stores/conversation";
import { useProjectStore } from "../stores/project";
import { useToolsStore } from "../stores/tools";
import { useUiStore } from "../stores/ui";

/**
 * 工作台引导（评审 R8 拆分自 App.tsx 的 Workspace）：
 *   ① 跨项目 reset（conversation/tools/ui/project 四 store）；
 *   ② GET /projects/{id} → project.loadSnapshot（右栏五面板的 REST 侧唯一数据源）；
 *   ③ GET /messages → conversation.hydrate（刷新恢复对话史，含首页首问）；
 *   ④ App Token 401 → 人话横幅 + 就地弹密钥层（由调用方注入 onAuthError）。
 *
 * **两条硬约束写在代码里，不靠记忆**：
 * - reset effect 必须声明在拉取 effect **之前**（React 按声明顺序执行 effect），
 *   否则 hydrate 落在清空之前，历史会被自己清掉；
 * - 成功回调只撤**同 code** 的降级横幅（见 dismissBannerIf）——兄弟请求失败侧的
 *   横幅不得被成功侧误撤，错误指示丢失比横幅多留更糟。
 *
 * @param retryToken 令牌变更计数：密钥层保存了新 App Token 时 +1，重跑本引导
 *                   （免整页刷新）。由 useAuthGate 提供。
 */
export function useProjectBootstrap(
  projectId: string,
  opts: { onAuthError: () => void; retryToken?: number },
) {
  const { onAuthError, retryToken = 0 } = opts;
  const loadSnapshot = useProjectStore((s) => s.loadSnapshot);

  // 经 ref 中转：onAuthError 常是内联箭头（每渲染新引用），进依赖会让引导 effect
  // 每次渲染都重跑一遍拉取。
  const onAuthErrorRef = useRef(onAuthError);
  useEffect(() => { onAuthErrorRef.current = onAuthError; });

  // ① 跨项目 reset：按 projectId 变化清空四 store（幂等，StrictMode 双执行安全）
  useEffect(() => {
    useConversationStore.getState().reset();
    useToolsStore.getState().reset();
    useUiStore.getState().reset();
    useProjectStore.getState().reset();
  }, [projectId]);

  // ②③ 快照 + 对话史并列拉取（互不阻塞）；失败如实上屏，不静默吞错、不伪造空快照
  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    rest
      .getProject(projectId)
      .then((snap) => {
        if (alive) {
          loadSnapshot(snap);
          dismissBannerIf("snapshot_failed");   // 令牌重试成功的主场景：只撤同 code 横幅
        }
      })
      .catch((err: unknown) => {
        if (!alive) return;
        if (isAppTokenError(err)) {
          reportAuthError("snapshot_failed");
          onAuthErrorRef.current();
          return;
        }
        useUiStore.getState().applyEvent({
          event: "error",
          code: "snapshot_failed",
          message: "项目快照加载失败，请稍后重试",  // 友好文案：不透出 String(err)；err 只用于上方令牌判定
          hint: "请刷新重试",
        });
      });
    rest
      .getMessages(projectId)
      .then((msgs) => {
        if (alive) {
          useConversationStore.getState().hydrate(msgs);
          dismissBannerIf("history_failed");
        }
      })
      .catch((err: unknown) => {
        if (!alive) return;
        if (isAppTokenError(err)) {
          reportAuthError("history_failed");
          onAuthErrorRef.current();
          return;
        }
        useUiStore.getState().applyEvent({
          event: "error",
          code: "history_failed",
          message: "对话历史加载失败，请稍后重试",
          hint: "请刷新重试",
        });
      });
    return () => {
      alive = false;
    };
  }, [projectId, loadSnapshot, retryToken]);
}

/** App Token 守卫 401（web_host/app.py 审计 F02）的共用降级：人话横幅如实保留来源
 *  code，不透传后端 JSON 响应体；弹层由调用方就地打开。 */
export function reportAuthError(code: "snapshot_failed" | "history_failed") {
  useUiStore.getState().applyEvent({
    event: "error",
    code,
    message: "访问令牌缺失或已失效，无法加载项目数据",
    hint: "请在「密钥与登录」弹层填写访问令牌并保存",
  });
}

/** 成功回调只撤同 code 的降级横幅：dismissBanner 是无条件清空单一槽位，
 *  兄弟请求失败侧（如快照 500 + 消息史成功）或其他来源（SSE 错误等）的
 *  横幅不得被成功侧误撤——错误指示丢失比横幅多留更糟。 */
export function dismissBannerIf(code: string) {
  const ui = useUiStore.getState();
  if (ui.degradedBanner?.code === code) ui.dismissBanner();
}
