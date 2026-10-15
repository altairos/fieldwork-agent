import { BrowserRouter, Route, Routes } from "react-router-dom";

// 三路由占位：后续 Task 分别替换为 ProjectHub / Workspace / ProposalView 真实页面
function ProjectHub() {
  return (
    <div className="font-serif text-ink">
      <h1 className="text-2xl font-semibold">田野罗盘 · 项目中枢</h1>
      <p className="text-inksoft">ProjectHub 占位（Task 3 实现）</p>
    </div>
  );
}

function Workspace() {
  return (
    <div className="font-serif text-ink">
      <h1 className="text-2xl font-semibold">田野罗盘 · 工作区</h1>
      <p className="text-inksoft">Workspace 占位（Task 4+ 实现）</p>
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
