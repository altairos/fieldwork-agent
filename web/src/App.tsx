import { useEffect, useState } from "react";
import { BrowserRouter, Link, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { rest } from "./api/rest";
import { Composer } from "./components/chat/Composer";
import { PhaseBanner } from "./components/chat/PhaseBanner";
import { Timeline } from "./components/chat/Timeline";
import { Header } from "./components/layout/Header";
import { LeftRail } from "./components/layout/LeftRail";
import { RightDock } from "./components/layout/RightDock";
import { IconCaret } from "./components/ui/icons";
import { useTurn } from "./hooks/useTurn";
import { hasDashScopeKey } from "./lib/keys";
import { useConversationStore } from "./stores/conversation";
import { useProjectStore } from "./stores/project";
import { useToolsStore } from "./stores/tools";
import { useUiStore, PHASE_TAB } from "./stores/ui";
import { ProjectHub } from "./routes/ProjectHub";
import { ProposalView } from "./routes/ProposalView";

/**
 * Workspace 三栏骨架（设计板 lines 388-674）：
 * Header 顶栏 + 左栏 PhaseRail(212px) + 中栏对话流(1fr) + 右栏上下文面板(340px)。
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
  const turn = useTurn(projectId);
  const [composerLocked, setComposerLocked] = useState(false);

  // 跨项目 reset：按 projectId 变化清空四 store（幂等，StrictMode 双执行安全）
  useEffect(() => {
    useConversationStore.getState().reset();
    useToolsStore.getState().reset();
    useUiStore.getState().reset();
    useProjectStore.getState().reset();
  }, [projectId]);

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

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    rest
      .getProject(projectId)
      .then((snap) => {
        if (alive) loadSnapshot(snap);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        useUiStore.getState().applyEvent({
          event: "error",
          code: "snapshot_failed",
          message: `项目快照加载失败：${String(err)}`,
          hint: "请刷新重试",
        });
      });
    return () => {
      alive = false;
    };
  }, [projectId, loadSnapshot]);

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
      <div className="flex-1 grid grid-cols-1 grid-rows-[1fr_auto] lg:grid-cols-[212px_1fr_340px] lg:grid-rows-1 min-h-0">
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
            onSelect={(pid) => useUiStore.getState().setActiveTab(PHASE_TAB[pid] ?? "progress")}
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
                未配置百炼 API Key：请回
                <Link to="/" className="text-indigo underline underline-offset-2 mx-0.5">
                  首页「API 密钥」卡
                </Link>
                填写并保存后返回本页。
              </span>
            }
          />
        </main>
        {/* 右栏：lg+ 常驻网格列；lg- 折叠为底部面板（rightCollapsed 控制，把手切换）。
            收起时只留把手横条；展开时面板 62vh，内部 Tabs/滚动结构不变（RightDock 本体不改） */}
        <div className="min-h-0 min-w-0 flex flex-col border-t border-hairline lg:border-t-0">
          <button
            type="button"
            onClick={toggleRight}
            aria-expanded={!rightCollapsed}
            className="lg:hidden h-10 shrink-0 flex items-center justify-between px-4 bg-paperdeep text-[10.5px] tracking-[.16em] text-inkfaint"
          >
            上下文面板 · 状态投影
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
    </div>
  );
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
