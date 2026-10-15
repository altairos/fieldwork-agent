import { BrowserRouter, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { Header } from "./components/layout/Header";
import { LeftRail } from "./components/layout/LeftRail";
import { RightDock } from "./components/layout/RightDock";
import { EmptyState } from "./components/ui/EmptyState";
import { IconCompass } from "./components/ui/icons";
import { useProjectStore } from "./stores/project";

// 三路由占位：后续 Task 分别替换为 ProjectHub / Workspace / ProposalView 真实页面
function ProjectHub() {
  return (
    <div className="font-serif text-ink">
      <h1 className="text-2xl font-semibold">田野罗盘 · 项目中枢</h1>
      <p className="text-inksoft">ProjectHub 占位（Task 3 实现）</p>
    </div>
  );
}

/**
 * Workspace 三栏骨架（设计板 lines 388-674）：
 * Header 顶栏 + 左栏 PhaseRail(212px) + 中栏对话流占位(1fr) + 右栏上下文面板(340px)。
 * 中栏对话流与各面板内容由 Task 7 填充。
 */
function Workspace() {
  const { id } = useParams();
  const navigate = useNavigate();
  const phase = useProjectStore((s) => s.phase);
  const topic = useProjectStore((s) => s.snapshot?.research_context?.topic);
  return (
    <div className="h-full flex flex-col">
      <Header
        projectName={topic || "未命名项目"}
        phase={phase}
        view="workspace"
        onNavigate={(v) => {
          if (v === "proposal") navigate(`/p/${id}/proposal`);
        }}
      />
      <div className="flex-1 grid grid-cols-[212px_1fr_340px] min-h-0">
        <LeftRail phase={phase} onBack={() => navigate("/")} />
        <main className="bg-paper/40 min-w-0 flex flex-col items-center justify-center">
          <EmptyState
            icon={<IconCompass className="w-4 h-4" />}
            title="对话流建设中"
            hint="阶段访谈与工具结果卡将在此呈现（Task 7 接入 SSE 时间线）"
          />
        </main>
        <RightDock />
      </div>
    </div>
  );
}

function ProposalView() {
  return (
    <div className="font-serif text-ink">
      <h1 className="text-2xl font-semibold">田野罗盘 · 开题报告</h1>
      <p className="text-inksoft">ProposalView 占位（后续 Task 实现）</p>
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
