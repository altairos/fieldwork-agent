import { type CSSProperties, useEffect, useRef, useState } from "react";
import { BrowserRouter, Link, Route, Routes, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { isAppTokenError, rest } from "./api/rest";
import { Composer } from "./components/chat/Composer";
import { PhaseBanner } from "./components/chat/PhaseBanner";
import { Timeline } from "./components/chat/Timeline";
import { Header } from "./components/layout/Header";
import { LeftRail } from "./components/layout/LeftRail";
import { RightDock } from "./components/layout/RightDock";
import { RightResizeHandle, loadRightWidth, RIGHT_W_KEY } from "./components/layout/RightResizeHandle";
import { KeyDialog } from "./components/settings/KeyDialog";
import { IconCaret } from "./components/ui/icons";
import { useTurn } from "./hooks/useTurn";
import { getKeys, hasDashScopeKey } from "./lib/keys";
import { useConversationStore } from "./stores/conversation";
import { useProjectStore } from "./stores/project";
import { useToolsStore } from "./stores/tools";
import { useUiStore, PHASE_TAB } from "./stores/ui";
import { ProjectHub } from "./routes/ProjectHub";
import { ProposalView } from "./routes/ProposalView";

/**
 * Workspace 三栏骨架（设计板 lines 388-674）：
 * Header 顶栏 + 左栏 PhaseRail(212px) + 中栏对话流(1fr) + 右栏上下文面板
 * （默认 340px，P3 可拖拽调宽 280–560：--right-w CSS 变量驱动 grid 第三列，
 *   拖拽结果 localStorage 持久化，刷新/重挂载恢复）。
 *
 * 中栏（设计板 line 426 `flex flex-col bg-paper/40 min-w-0`）= PhaseBanner(428-432)
 *   + Timeline(433-514，flex-1 滚动) + Composer(516-526)。
 * 回合所有权单一：`useTurn(projectId)` 只在此处调用一次，句柄注入 Composer
 *   （Composer 自身不再建第二个回合，避免并发 SSE 流）。
 * 初始快照：`GET /projects/{id}` → `project.loadSnapshot`（右栏五面板的 REST 侧数据源）；
 *   失败经 ui store 的 error 事件如实上屏（PhaseBanner 的降级横幅），不静默吞错、不伪造空快照。
 * 跨项目 reset（Task 7 遗留，Task 8 补齐）：projectId 变化时先清空 conversation/tools/ui/project
 *   四个 store，避免上一个项目的对话/工具卡/横幅状态串台；该 effect 声明在快照加载之前，
 *   React 按声明顺序执行，保证「先清后载」。
 * 无 key 拦截（可浏览不可发）：挂载时查本地 key + GET /settings/status，
 * 本地无百炼 key 且服务端未配 → Composer locked；状态查询失败不锁
 * （后端 403 llm_key_required 兑底，见 useTurn keyMissing）。
 * 移动端（<lg，批次 A）：三栏回落单栏——左栏抽屉化（ui store 的 leftCollapsed，
 *   Header 汉堡钮触发，backdrop/Escape 关闭）；右栏折叠为底部面板（rightCollapsed，
 *   把手切换）；桌面（lg+）布局不变（CSS 断点控制，JS 状态只管移动形态）。
 * App Token 401（服务端启用 FIELDWORK_APP_TOKEN 后浏览器未存/失效）：横幅给人话
 *   指引（不透传后端 JSON）并就地弹 KeyDialog；保存新令牌后关闭即带新令牌重拉
 *   快照与消息史（authRetry 重跑本 effect），免整页刷新。
 */
function Workspace() {
  const { id } = useParams();
  const projectId = id ?? "";
  const navigate = useNavigate();
  const phase = useProjectStore((s) => s.phase);
  const topic = useProjectStore((s) => s.snapshot?.research_context?.topic);
  const loadSnapshot = useProjectStore((s) => s.loadSnapshot);
  const leftCollapsed = useUiStore((s) => s.leftCollapsed);
  const rightCollapsed = useUiStore((s) => s.rightCollapsed);
  const toggleLeft = useUiStore((s) => s.toggleLeft);
  const toggleRight = useUiStore((s) => s.toggleRight);
  // 发送链路守卫 401 → 就地弹密钥层（免回首页）；箭头体延迟求值 openKeyDialog
  // （其 const 定义在下方，此处直引会碰 TDZ）。
  const turn = useTurn(projectId, { onAuthError: () => openKeyDialog() });
  const turnAdopt = turn.adopt;   // 解构出稳定引用再进 effect 依赖（turn 对象每渲染新引用）
  const [searchParams] = useSearchParams();
  const [composerLocked, setComposerLocked] = useState(false);
  const [keyDialogOpen, setKeyDialogOpen] = useState(false);
  const [authRetry, setAuthRetry] = useState(0);   // 401 弹层保存新令牌后 +1 → 重拉数据
  const tokenBeforeDialog = useRef("");             // 弹层打开前的令牌快照（关闭时比对）
  // P3 右栏拖拽调宽：宽度状态由 Workspace 持有（--right-w 须设在 grid 祖先容器，
  // 把手组件作用域够不着），把手只上抛新值
  const [rightWidth, setRightWidth] = useState(loadRightWidth);

  // 跨项目 reset：按 projectId 变化清空四 store（幂等，StrictMode 双执行安全）
  useEffect(() => {
    useConversationStore.getState().reset();
    useToolsStore.getState().reset();
    useUiStore.getState().reset();
    useProjectStore.getState().reset();
  }, [projectId]);

  // P5 bfcache 恢复：回首页保存 key 后后退回本页时，页面从缓存原样恢复（React
  // 不重挂载，上面的判定 effect 不重跑）→ persisted pageshow 重查一次，
  // 避免已保存的 key 被旧状态误判为“未配置”
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

  // P3 拖拽结果持久化：宽度一变即写 localStorage（clamp 由把手组件保证在界内）
  useEffect(() => {
    localStorage.setItem(RIGHT_W_KEY, String(rightWidth));
  }, [rightWidth]);

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

  // 401 就地弹密钥层（不必回首页找入口）；关闭时若保存过新令牌 → 就地重试；
  // P5：弹层里补填了百炼 Key → 就地解锁（原判定只在挂载时查一次，
  // 回首页保存后返回未重查 → 误显“未配置百炼 API Key”）
  const openKeyDialog = () => {
    tokenBeforeDialog.current = getKeys().appToken;
    setKeyDialogOpen(true);
  };
  const closeKeyDialog = () => {
    setKeyDialogOpen(false);
    if (getKeys().appToken !== tokenBeforeDialog.current) setAuthRetry((n) => n + 1);
    if (hasDashScopeKey()) setComposerLocked(false);
  };

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
          openKeyDialog();
          return;
        }
        useUiStore.getState().applyEvent({
          event: "error",
          code: "snapshot_failed",
          message: "项目快照加载失败，请稍后重试",  // 友好文案（2026-10-18）：不透出 String(err)；err 供上方令牌判定
          hint: "请刷新重试",
        });
      });
    // 对话史回填（刷新恢复）：与快照并列拉取互不阻塞；reset effect 先于本 effect
    // 声明执行，hydrate 一定落在清空之后。失败如实上屏，不静默留"尚无对话"假象。
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
          openKeyDialog();
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
  }, [projectId, loadSnapshot, authRetry]);

  // 首页两步创建携带的首轮回合（?turn=，轮 4）：接住后立即 replace 清 query
  // （防刷新重放/分享链接重触发），再订阅该回合的 SSE 回放——首页输入由此成为
  // 对话页第一条消息。replace 后 searchParams 变化触发重跑，turnId 已空则早退。
  useEffect(() => {
    const turnId = searchParams.get("turn");
    if (!projectId || !turnId) return;
    navigate(`/p/${projectId}`, { replace: true });
    void turnAdopt(turnId);
  }, [projectId, searchParams, navigate, turnAdopt]);

  // Escape 关闭移动端抽屉（对齐 ThemeSwitcher 键盘关闭惯例）；桌面无抽屉，无副作用
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !useUiStore.getState().leftCollapsed) toggleLeft();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [toggleLeft]);

  return (
    <div className="h-full flex flex-col">
      <Header
        projectName={topic || "未命名项目"}
        phase={phase}
        view="workspace"
        onMenu={toggleLeft}
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
            className="fixed inset-0 z-30 bg-ink/35 fade-in lg:hidden"
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
            onBack={() => navigate("/")}
            onSelect={(pid) => useUiStore.getState().setActiveTab(PHASE_TAB[pid] ?? "basic-info")}
          />
        </div>
        <main className="flex flex-col bg-paper/40 min-w-0 min-h-0">
          <PhaseBanner />
          <Timeline />
          <Composer
            turn={turn}
            locked={composerLocked}
            lockHint={
              <span>
                未配置百炼 API Key：
                <button
                  type="button"
                  onClick={openKeyDialog}
                  className="text-indigo underline underline-offset-2 mx-0.5"
                >
                  填写百炼 API Key
                </button>
                ，或回
                <Link to="/" className="text-indigo underline underline-offset-2 mx-0.5">
                  首页「密钥 / 登录」
                </Link>
                填写并保存后返回本页。
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
      {/* 401 就地弹密钥层：保存新令牌后关闭即带新令牌重拉（免整页刷新） */}
      <KeyDialog open={keyDialogOpen} onClose={closeKeyDialog} />
    </div>
  );
}

/** App Token 守卫 401（web_host/app.py 审计 F02）的共用降级：人话横幅如实保留来源
 *  code，不透传后端 JSON 响应体；弹层由调用方就地打开。 */
function reportAuthError(code: "snapshot_failed" | "history_failed") {
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
function dismissBannerIf(code: string) {
  const ui = useUiStore.getState();
  if (ui.degradedBanner?.code === code) ui.dismissBanner();
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<ProjectHub />} />
        <Route path="/p/:id" element={<Workspace />} />
        <Route path="/p/:id/proposal" element={<ProposalView />} />
      </Routes>
    </BrowserRouter>
  );
}
