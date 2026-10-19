import { useState } from "react";
import { Chip } from "../ui/Chip";
import { ThemeSwitcher } from "../ui/ThemeSwitcher";
import { IconMenu } from "../ui/icons";
import { DEFAULT_PHASES } from "./LeftRail";
import { useAuthStore } from "../../stores/auth";

/**
 * Header — 工作台顶栏。
 * 设计板同源：
 *   顶栏基座（设计板自身 header，lines 95-96）：`sticky top-0 z-40 backdrop-blur bg-paper/85 border-b border-hairline` + `h-14 flex items-center gap-6`
 *   左品牌区（lines 97-103）：logo + 「田野罗盘 / FIELDWORK COMPASS」双行 + 竖分隔线（line 104）
 *   项目名（line 399 左栏项目名样式 `text-inkfaint truncate` 提级为顶栏主名）+ 阶段徽标
 *     （chip 样式沿用项目卡 dot；文案 = 阶段名 + 进行中后缀「××中」，与左栏「A · 研究定位」
 *     静态清单式呈现区分——顶栏表达的是当前进行状态）
 *   右视图切换（tabbtn，lines 74/106-109）：`px-3 h-8 rounded-md border border-transparent text-inksoft hover:text-ink hover:bg-cardsunk`，
 *     选中态（line 74 `.tabbtn[aria-selected="true"]`）：`text-cinnabar border-cinnabar`
 *   （2026-10-18：导出按钮已删——提案页左栏已有完整下载区，顶栏重复入口移除。）
 * 移动端（<lg，批次 A）：左侧汉堡钮（onMenu 触发 Workspace 左栏抽屉，仅传 prop 时渲染），
 *   品牌双行文字/分隔线 md+ 展开，配色按钮文字 sm+ 展开（图标常驻），
 *   边距与间距收窄——避免单行顶栏在小屏溢出。
 * 账户区（账号隔离一期）：未登录「登录」钮（onOpenAuth 打开 AuthDialog，仅传 prop 时渲染，
 *   同 onMenu 惯例）；已登录显示邮箱 + 「退出」（useAuthStore.logout，点即登出回落匿名）。
 */
export type WorkspaceView = "workspace" | "proposal";

export function Header({
  projectName,
  phase,
  view = "workspace",
  onNavigate,
  onMenu,
  onRename,
  onOpenAuth,
}: {
  projectName?: string;
  /** 当前阶段 id（research_positioning 等，对齐 DEFAULT_PHASES） */
  phase?: string;
  view?: WorkspaceView;
  onNavigate?: (view: WorkspaceView) => void;
  /** 移动端汉堡钮（触发 Workspace 左栏抽屉）；不传则不渲染（如 ProposalView 无抽屉） */
  onMenu?: () => void;
  /** 项目名就地改名提交（任务1：trim 非空且变化才上行）；不传则项目名纯展示 */
  onRename?: (name: string) => void;
  /** 打开登录/注册弹窗（账号隔离一期）；不传则不渲染账户区 */
  onOpenAuth?: () => void;
}) {
  const meta = DEFAULT_PHASES.find((p) => p.id === phase);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  // 提交口径：trim 非空且与当前名不同才上行；Esc 取消；blur 与 Enter 同一道提交
  const commitRename = () => {
    const name = draft.trim();
    if (name !== "" && name !== projectName) onRename?.(name);
    setEditing(false);
  };
  const views: { id: WorkspaceView; label: string }[] = [
    { id: "workspace", label: "工作台" },
    { id: "proposal", label: "方案草稿" },
  ];
  return (
    <header className="sticky top-0 z-40 backdrop-blur bg-paper/85 border-b border-hairline">
      <div className="px-4 sm:px-6 h-14 flex items-center gap-3 sm:gap-6">
        {onMenu && (
          <button
            type="button"
            onClick={onMenu}
            aria-label="打开阶段导航"
            className="lg:hidden -ml-1.5 h-9 w-9 shrink-0 rounded-md text-inksoft hover:text-ink hover:bg-cardsunk transition inline-flex items-center justify-center"
          >
            <IconMenu className="w-5 h-5" />
          </button>
        )}
        {/* 品牌区（移动端只留 logo，双行文字 md+ 展开） */}
        <div className="flex items-center gap-2.5 shrink-0">
          <img src="/fieldwork-logo-full.svg" alt="" className="w-7 h-7" />
          <div className="leading-tight hidden md:block">
            <div className="font-serif text-[15px] font-semibold tracking-wide">田野罗盘</div>
            <div className="text-[10px] text-inkfaint tracking-[.18em] num">FIELDWORK COMPASS</div>
          </div>
        </div>
        <div className="h-6 w-px bg-hairline hidden md:block" aria-hidden="true" />
        {/* 项目名 + 阶段徽标（任务1：项目名可就地改名——提供 onRename 时点击进编辑，
            Enter/失焦提交（IME 组合中的 Enter 不触发）、Esc 取消；否则纯展示） */}
        <div className="flex items-center gap-2 min-w-0">
          {editing ? (
            <input
              aria-label="项目名称"
              value={draft}
              maxLength={30}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) commitRename();
                else if (e.key === "Escape") setEditing(false);
              }}
              onBlur={commitRename}
              className="font-serif text-[14px] font-semibold hairline rounded-md px-1.5 py-0.5 bg-cardsunk outline-none focus:border-cinnabar w-40 shrink-0"
            />
          ) : projectName && (onRename ? (
            <button
              type="button"
              title="点击修改项目名称"
              onClick={() => {
                setDraft(projectName);
                setEditing(true);
              }}
              className="font-serif text-[14px] font-semibold truncate hover:text-cinnabar transition cursor-text"
            >
              {projectName}
            </button>
          ) : (
            <span className="font-serif text-[14px] font-semibold truncate">{projectName}</span>
          ))}
          {meta && (
            <Chip tone="cinnabar" className="shrink-0">
              <span className="dot bg-cinnabar" aria-hidden="true" />
              {meta.label}中
            </Chip>
          )}
        </div>
        {/* 右：视图切换 + 配色 + 账户（tablist 只包视图 tab，menu/普通按钮不入 tablist） */}
        <div className="ml-auto flex items-center gap-1 text-[13px]">
          <div className="flex items-center gap-1" role="tablist">
            {views.map((v) => (
              <button
                key={v.id}
                type="button"
                role="tab"
                aria-selected={view === v.id}
                onClick={() => onNavigate?.(v.id)}
                className={`px-2 sm:px-3 h-8 rounded-md border transition ${
                  view === v.id
                    ? "text-cinnabar border-cinnabar"
                    : "border-transparent text-inksoft hover:text-ink hover:bg-cardsunk"
                }`}
              >
                {v.label}
              </button>
            ))}
          </div>
          <ThemeSwitcher />
          {onOpenAuth && (
            user ? (
              <div className="flex items-center gap-1.5 shrink-0">
                <span
                  title={user.email}
                  className="hidden sm:inline max-w-[14ch] truncate text-[12.5px] text-inksoft"
                >
                  {user.email}
                </span>
                {/* 网络失败不打扰：本地清理已在 store 的 finally 完成 */}
                <button
                  type="button"
                  onClick={() => void logout().catch(() => {})}
                  className="px-2.5 h-8 rounded-md border border-transparent text-inksoft hover:text-ink hover:bg-cardsunk transition cursor-pointer"
                >
                  退出
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={onOpenAuth}
                className="px-2.5 h-8 rounded-md border border-transparent text-inksoft hover:text-ink hover:bg-cardsunk transition cursor-pointer shrink-0"
              >
                登录
              </button>
            )
          )}
        </div>
      </div>
    </header>
  );
}
