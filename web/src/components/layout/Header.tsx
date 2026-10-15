import { Chip } from "../ui/Chip";
import { IconDownload } from "../ui/icons";
import { DEFAULT_PHASES } from "./LeftRail";

/**
 * Header — 工作台顶栏。
 * 设计板同源：
 *   顶栏基座（设计板自身 header，lines 95-96）：`sticky top-0 z-40 backdrop-blur bg-paper/85 border-b border-hairline` + `h-14 flex items-center gap-6`
 *   左品牌区（lines 97-103）：logo + 「田野罗盘 / FIELDWORK COMPASS」双行 + 竖分隔线（line 104）
 *   项目名（line 399 左栏项目名样式 `text-inkfaint truncate` 提级为顶栏主名）+ 阶段徽标（line 322 项目卡 chip：dot + 「C 行程规划」）
 *   右视图切换（tabbtn，lines 74/106-109）：`px-3 h-8 rounded-md border border-transparent text-inksoft hover:text-ink hover:bg-cardsunk`，
 *     选中态（line 74 `.tabbtn[aria-selected="true"]`）：`text-cinnabar border-cinnabar`
 *   导出按钮（line 243「下载 .md」outline + line 134 download 图标）。
 */
export type WorkspaceView = "workspace" | "proposal";

export function Header({
  projectName,
  phase,
  view = "workspace",
  onNavigate,
  onExport,
}: {
  projectName?: string;
  /** 当前阶段 id（research_positioning 等，对齐 DEFAULT_PHASES） */
  phase?: string;
  view?: WorkspaceView;
  onNavigate?: (view: WorkspaceView) => void;
  onExport?: () => void;
}) {
  const meta = DEFAULT_PHASES.find((p) => p.id === phase);
  const views: { id: WorkspaceView; label: string }[] = [
    { id: "workspace", label: "工作台" },
    { id: "proposal", label: "提案" },
  ];
  return (
    <header className="sticky top-0 z-40 backdrop-blur bg-paper/85 border-b border-hairline">
      <div className="px-6 h-14 flex items-center gap-6">
        {/* 品牌区 */}
        <div className="flex items-center gap-2.5 shrink-0">
          <img src="/fieldwork-logo-full.svg" alt="" className="w-7 h-7" />
          <div className="leading-tight">
            <div className="font-serif text-[15px] font-semibold tracking-wide">田野罗盘</div>
            <div className="text-[10px] text-inkfaint tracking-[.18em] num">FIELDWORK COMPASS</div>
          </div>
        </div>
        <div className="h-6 w-px bg-hairline" aria-hidden="true" />
        {/* 项目名 + 阶段徽标 */}
        <div className="flex items-center gap-2 min-w-0">
          {projectName && (
            <span className="font-serif text-[14px] font-semibold truncate">{projectName}</span>
          )}
          {meta && (
            <Chip tone="cinnabar" className="shrink-0">
              <span className="dot bg-cinnabar" aria-hidden="true" />
              {meta.letter} {meta.label}
            </Chip>
          )}
        </div>
        {/* 右：视图切换 + 导出 */}
        <div className="ml-auto flex items-center gap-1 text-[13px]" role="tablist">
          {views.map((v) => (
            <button
              key={v.id}
              type="button"
              role="tab"
              aria-selected={view === v.id}
              onClick={() => onNavigate?.(v.id)}
              className={`px-3 h-8 rounded-md border transition ${
                view === v.id
                  ? "text-cinnabar border-cinnabar"
                  : "border-transparent text-inksoft hover:text-ink hover:bg-cardsunk"
              }`}
            >
              {v.label}
            </button>
          ))}
          <button
            type="button"
            onClick={onExport}
            className="ml-3 h-8 px-3 rounded-md hairline bg-card text-ink text-[12.5px] hover:bg-cardsunk transition inline-flex items-center gap-1.5"
          >
            <IconDownload className="w-4 h-4" />
            导出
          </button>
        </div>
      </div>
    </header>
  );
}
