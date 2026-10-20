import { type CSSProperties, lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AuthDialog } from "./components/auth/AuthDialog";
import { Composer } from "./components/chat/Composer";
import { EvidencePanel } from "./components/chat/EvidencePanel";
import { PhaseBanner } from "./components/chat/PhaseBanner";
import { Timeline } from "./components/chat/Timeline";
import { Header } from "./components/layout/Header";
import { LeftRail } from "./components/layout/LeftRail";
import { RightDock } from "./components/layout/RightDock";
import { RightResizeHandle } from "./components/layout/RightResizeHandle";
import { Tabs, tabIds } from "./components/ui/Tabs";
import { IconCaret } from "./components/ui/icons";
import { useAuthGate } from "./hooks/useAuthGate";
import { disposeGuestProject, useDisposeGuestProject } from "./hooks/useDisposeGuestProject";
import { useMobileDrawer } from "./hooks/useMobileDrawer";
import { useProjectBootstrap } from "./hooks/useProjectBootstrap";
import { useRenameProject } from "./hooks/useRenameProject";
import { useRightWidth } from "./hooks/useRightWidth";
import { useTurn } from "./hooks/useTurn";
import { getSessionToken } from "./lib/session";
import { useAuthStore } from "./stores/auth";
import { useProjectStore } from "./stores/project";
import { useUiStore, PHASE_TAB, type MiddleTab } from "./stores/ui";

/**
 * 路由级代码分割（评审 R9）：原先 ProjectHub / ProposalView 与工作台全部打进同一个
 * 560 kB 主 chunk，首屏（绝大多数用户是 `/` 首页）要下完提案阅读器 + 全部卡片才开画。
 * 现在两条独立路由各自成 chunk，主包只留工作台骨架（路由切换时由 Suspense 兜一次）。
 *
 * 补 `default` 包装而不改路由文件的导出形状：`routes/*` 用具名导出（测试直接 import
 * 具名组件），lazy 需要 default。
 */
const ProjectHub = lazy(() =>
  import("./routes/ProjectHub").then((m) => ({ default: m.ProjectHub })));
const ProposalView = lazy(() =>
  import("./routes/ProposalView").then((m) => ({ default: m.ProposalView })));

/** 路由懒加载的兜底：与时间线"加载中"同一视觉语言（旋转 logo，不闪白屏）。 */
function RouteFallback() {
  return (
    <div className="h-full grid place-items-center">
      <img src={`${import.meta.env.BASE_URL}fieldwork-logo-full.svg`} alt="" className="w-6 h-6 animate-spin opacity-70" />
    </div>
  );
}

/**
 * Workspace 三栏骨架（设计板 lines 388-674）：
 * Header 顶栏 + 左栏 PhaseRail(212px) + 中栏对话流(1fr) + 右栏上下文面板
 * （默认 340px，P3 可拖拽调宽 280–560：--right-w CSS 变量驱动 grid 第三列，
 *   拖拽结果 localStorage 持久化，刷新/重挂载恢复）。
 *
 * 中栏（设计板 line 426 `flex flex-col bg-paper/40 min-w-0`）= PhaseBanner(428-432)
 *   + 「对话/检索结果」双 tab（spec C1）+ Timeline(433-514，flex-1 滚动)
 *   + Composer(516-526)。
 *
 * 回合所有权单一：`useTurn(projectId)` 只在此处调用一次，句柄注入 Composer
 *   （Composer 自身不再建第二个回合，避免并发 SSE 流）。
 *
 * **职责拆分（评审 R8）**：本组件只负责布局与接线，原先压在它身上的七件事已各自成 hook——
 *   跨项目/跨身份 reset + 快照/历史回填 → `useProjectBootstrap`（A02：登录用户也在 reset
 *   维度内，先清空四 store 再按新身份重拉，避免上一身份的对话/工具卡/横幅串台）；
 *   无 key 拦截 + 401 弹层 + bfcache 复查 + 会话失效弹窗 → `useAuthGate`；
 *   移动端抽屉/Escape → `useMobileDrawer`；
 *   右栏宽度持久化 → `useRightWidth`；
 *   Header 改名 → `useRenameProject`；
 *   回合生命周期 → `useTurn`（原有）；
 *   `?turn=` 领养首页创建的回合 → 本组件内 6 行（它同时要改 URL，与路由绑死）。
 *
 * 登录/注册弹窗（AuthDialog，账号一期）：Header「登录」钮 + 401 分流（App 令牌类自动
 *   展开「高级 · API 密钥」折叠区；会话失效只重登不展开）共用；关闭时保存过新令牌
 *   → 就地重拉快照与消息史（authRetry 重跑引导 effect），免整页刷新。KeyDialog 已退役。
 */
const MIDDLE_IDS = tabIds("middle");

function Workspace() {
  const { id } = useParams();
  const projectId = id ?? "";
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const phase = useProjectStore((s) => s.phase);
  // 匿名会话（未注册）离开时清除项目记录：只有注册账号名下的对话才长期保存
  // （App Token 是参赛演示用，匿名记录不留存 —— 见 useDisposeGuestProject 顶部注释）
  // 会话令牌尚在时不算匿名：慢网下 checkSession 未回时 user 恒为 null，
  // 照 !user 判定会在关页瞬间删掉登录用户的项目。
  const user = useAuthStore((s) => s.user);
  const guest = !user && !getSessionToken();
  useDisposeGuestProject(projectId, guest);
  // 项目显示名（任务1）：title（用户改名 / agent 自动命名）优先，未命名回退 topic；
  // 两者皆空由调用处兑底「未命名项目」——绝不回退 project_id（字母+数字不面向用户）。
  const projectName = useProjectStore(
    (s) => s.snapshot?.research_context?.title || s.snapshot?.research_context?.topic || "",
  );

  const {
    composerLocked, authDialogOpen, authKeysOpen, openAuthDialog, closeAuthDialog, authRetry,
  } = useAuthGate(projectId);
  const { leftCollapsed, rightCollapsed, toggleLeft, toggleRight } = useMobileDrawer();
  const { rightWidth, setRightWidth } = useRightWidth();
  const renameProject = useRenameProject(projectId);
  // App 令牌 401：就地弹登录窗并自动展开「高级 · API 密钥」（箭头体在渲染时才求值，
  // 不碰 openAuthDialog 的声明顺序）
  const openAuthDialogWithKeys = () => openAuthDialog(true);
  useProjectBootstrap(projectId, { onAuthError: openAuthDialogWithKeys, retryToken: authRetry });

  // 回合句柄（单一所有权）：发送链路守卫 401 时就地弹登录窗（免回首页）
  const turn = useTurn(projectId, { onAuthError: openAuthDialogWithKeys });
  const turnAdopt = turn.adopt;   // 解构出稳定引用再进 effect 依赖（turn 对象每渲染新引用）

  const middleTab = useUiStore((s) => s.middleTab);
  const evidenceHint = useUiStore((s) => s.evidenceHint);
  const setMiddleTab = useUiStore((s) => s.setMiddleTab);

  // 首页两步创建携带的首轮回合（?turn=，轮 4）：接住后立即 replace 清 query
  // （防刷新重放/分享链接重触发），再订阅该回合的 SSE 回放——首页输入由此成为
  // 对话页第一条消息。replace 后 searchParams 变化触发重跑，turnId 已空则早退。
  useEffect(() => {
    const turnId = searchParams.get("turn");
    if (!projectId || !turnId) return;
    navigate(`/p/${projectId}`, { replace: true });
    void turnAdopt(turnId);
  }, [projectId, searchParams, navigate, turnAdopt]);

  return (
    <div className="h-full flex flex-col">
      <Header
        projectName={projectName || "未命名项目"}
        phase={phase}
        view="workspace"
        onMenu={toggleLeft}
        onOpenAuth={() => openAuthDialog(false)}
        onRename={(name) => { void renameProject(name); }}
        onNavigate={(v) => {
          if (v === "proposal") navigate(`/p/${id}/proposal`);
        }}
      />
      <div
        className="flex-1 grid grid-cols-1 grid-rows-[1fr_auto] lg:grid-cols-[212px_1fr_var(--right-w)] lg:grid-rows-1 min-h-0"
        style={{ "--right-w": `${rightWidth}px` } as CSSProperties}
      >
        {/* 左栏：lg+ 常驻网格列；lg- 抽屉（leftCollapsed 控制，Header 汉堡钮触发）。
            抽屉展开时 fixed 脱离 grid 流（不占轨道），backdrop 点击收起；桌面恢复为 static 网格列。
            点击阶段条目只切右栏查看对应面板（PHASE_TAB）；阶段本身由内核可验证条件推进，
            点击不切阶段（产品红线） */}
        {!leftCollapsed && (
          <div
            className="fixed inset-0 z-30 bg-ink/35 fade-in fw-scrim lg:hidden"
            onClick={toggleLeft}
            aria-hidden="true"
          />
        )}
        <div
          className={
            leftCollapsed
              ? "min-h-0 hidden lg:flex"
              : "min-h-0 flex fixed inset-y-0 left-0 z-50 w-[268px] shadow-pop drawer-in lg:static lg:z-auto lg:w-auto lg:shadow-none"
          }
        >
          <LeftRail
            phase={phase}
            onBack={() => {
              // 匿名回首页也算“退出”：SPA 导航不触发 pagehide，这里显式删除
              if (guest) disposeGuestProject(projectId);
              navigate("/");
            }}
            onSelect={(pid) => useUiStore.getState().setActiveTab(PHASE_TAB[pid] ?? "basic-info")}
          />
        </div>
        <main className="flex flex-col bg-paper/40 min-w-0 min-h-0">
          <PhaseBanner />
          <div className="px-4 pt-3 shrink-0">
            {/* 中栏双 tab（spec C1）：对话 / 检索结果；红点=evidenceHint，绝不自动切（红线） */}
            <Tabs
              cols={2}
              idBase="middle"
              active={middleTab}
              onChange={(id) => setMiddleTab(id as MiddleTab)}
              tabs={[
                { id: "chat", label: "对话" },
                { id: "evidence", label: "检索结果", dot: evidenceHint || undefined },
              ]}
            />
          </div>
          {/* tabpanel：面板懒挂载（未选中不渲染，避免每次进工作台就拉证据索引） */}
          <div
            role="tabpanel"
            id={MIDDLE_IDS.panel(middleTab)}
            aria-labelledby={MIDDLE_IDS.tab(middleTab)}
            className="flex flex-col flex-1 min-h-0"
          >
            {middleTab === "chat" ? (
              <Timeline
                onRetry={() => { void turn.retry(); }}
                canRetry={turn.canRetry}
                retrying={turn.busy}
                incomplete={turn.incomplete}
              />
            ) : (
              <EvidencePanel projectId={projectId} />
            )}
          </div>
          <Composer
            turn={turn}
            locked={composerLocked}
            guest={guest}
            onRegister={() => openAuthDialog(false)}
            lockHint={
              <span>
                未配置百炼 API Key：
                <button
                  type="button"
                  onClick={() => openAuthDialog(true)}
                  className="text-indigo underline underline-offset-2 mx-0.5"
                >
                  在登录窗「高级 · API 密钥」中填写
                </button>
                ，保存后返回本页即可解锁。
              </span>
            }
          />
        </main>
        {/* 右栏：lg+ 常驻网格列；lg- 折叠为底部面板（rightCollapsed 控制，把手切换）。
            收起时只留把手横条；展开时面板 62vh，内部 Tabs/滚动结构不变（RightDock 本体不改） */}
        <div className="relative min-h-0 min-w-0 flex flex-col border-t border-hairline lg:border-t-0">
          {/* P3 桌面把手：右栏左边缘 separator 拖拽调宽（lg+ 显示，移动端无拖拽） */}
          <RightResizeHandle width={rightWidth} onResize={setRightWidth} />
          <button
            type="button"
            onClick={toggleRight}
            aria-expanded={!rightCollapsed}
            className="lg:hidden h-10 shrink-0 flex items-center justify-between px-4 bg-paperdeep text-[10.5px] tracking-[.16em] text-inkfaint"
          >
            上下文面板 · 实时状态
            <IconCaret
              className={`w-3.5 h-3.5 transition-transform duration-200 ${rightCollapsed ? "" : "rotate-90"}`}
            />
          </button>
          <div
            className={
              rightCollapsed
                ? "hidden lg:flex lg:flex-1 lg:min-h-0"
                : "flex h-[62vh] sheet-in lg:h-auto lg:flex-1 lg:min-h-0"
            }
          >
            <RightDock />
          </div>
        </div>
      </div>
      {/* 登录/注册弹窗：Header「登录」钮 + 401 分流（会话失效/App 令牌类）共用；
          关闭时保存过新令牌 → 就地重拉快照与消息史（免整页刷新） */}
      <AuthDialog open={authDialogOpen} onClose={closeAuthDialog} initialKeysOpen={authKeysOpen} />
    </div>
  );
}

export default function App() {
  // 挂载即核对会话（T14 checkSession 的首个产品调用点）：带 sess_ 刷新后恢复登录态
  // 显示；失效就地清令牌（不弹窗打断）；瞬时失败仅置 user:null、令牌保留
  useEffect(() => { void useAuthStore.getState().checkSession(); }, []);

  return (
    <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, "") || "/"}>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/" element={<ProjectHub />} />
          <Route path="/p/:id" element={<Workspace />} />
          <Route path="/p/:id/proposal" element={<ProposalView />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
