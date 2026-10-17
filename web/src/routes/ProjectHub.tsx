import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { rest } from "../api/rest";
import { DEFAULT_PHASES } from "../components/layout/LeftRail";
import { KeyDialog } from "../components/settings/KeyDialog";
import { IconCaret, IconClock, IconLock, IconSend } from "../components/ui/icons";
import type { ProjectSummary } from "../types/models";

/**
 * ProjectHub — 路由 `/`：极简首页（重设计）。
 * 视觉中央一句话输入：kicker + 品牌「田野罗盘」+ 提示语「描述你的田野调研想法」+
 *   大输入框（Enter 创建 / Shift+Enter 换行 / IME 组合中的 Enter 不触发）+
 *   三个示例 chips（点击填入完整示例文案并聚焦输入框）。
 * 右上角唯一常驻入口：「密钥 / 登录」→ KeyDialog 弹层（仅密钥配置；导出格式/配色
 *   等偏好不放首页——导出格式在提案页下载区，配色走对话页 Header）。
 * 已有项目收进输入区下方低调折叠入口「继续上次 · 我的项目（N）」：默认收起、
 *   无项目不渲染；列表加载失败如实提示但绝不阻塞创建（输入区始终可用，不等列表）。
 * 创建：POST createProject({ purpose }) → navigate(/p/:id)；跨项目 reset 由
 *   Workspace 挂载 effect 按 projectId 变化统一处理（App.tsx）。
 */

/** 示例 chips：短标签 + 点击填入的完整示例文案（文案与旧版 placeholder/模板同源） */
const EXAMPLES = [
  { label: "村落宗族组织研究", text: "研究西南少数民族村落的宗族组织变迁，单人驻村三个月，预算两万元" },
  { label: "非遗传承调查", text: "调查某项非遗技艺的传承现状与传承人谱系，为期两个月" },
  { label: "乡村教育民族志", text: "在一所乡村小学做教育民族志田野，观察课堂与家校互动" },
];

/** 阶段 → 状态点色（Tailwind 需静态类名，不可拼接；四色沿用旧版 ProjectCard 的 tone 映射） */
const DOT_BG: Record<string, string> = {
  research_positioning: "bg-inkfaint",
  field_site_profiles: "bg-indigo",
  itinerary_planning: "bg-cinnabar",
  proposal_generation: "bg-bamboo",
};

/** 更新时间 → 人读相对时间（沿用旧版） */
function fmtRelative(iso?: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const mins = Math.floor((Date.now() - t) / 60_000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  if (hours < 48) return "昨天";
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  return new Date(t).toLocaleDateString("zh-CN");
}

/** 折叠列表内的项目行：阶段点 + topic + 阶段名 + 相对时间，点击进入项目 */
function ProjectRow({ p, onOpen }: { p: ProjectSummary; onOpen: () => void }) {
  const meta = DEFAULT_PHASES.find((x) => x.id === p.phase);
  return (
    <button
      type="button"
      onClick={onOpen}
      className="hairline rounded-lg bg-card w-full flex items-center gap-3 px-4 h-11 text-left hover:border-cinnabar/40 hover:bg-cardsunk transition cursor-pointer"
    >
      <span className={`dot shrink-0 ${DOT_BG[p.phase] ?? "bg-inkfaint"}`} aria-hidden="true" />
      <span className="text-[13px] text-ink truncate min-w-0 flex-1">
        {p.research_context?.topic || p.project_id}
      </span>
      {meta && <span className="text-[11.5px] text-inksoft shrink-0">{meta.label}</span>}
      <span className="flex items-center gap-1 text-[11px] text-inkfaint shrink-0">
        <IconClock className="w-3.5 h-3.5" />
        {fmtRelative(p.updated_at ?? p.created_at)}
      </span>
    </button>
  );
}

export function ProjectHub() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [purpose, setPurpose] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [keyOpen, setKeyOpen] = useState(false);
  const [listOpen, setListOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    rest
      .listProjects()
      .then((list) => {
        if (alive) setProjects(list);
      })
      .catch((err: unknown) => {
        if (alive) setListError(`项目列表加载失败：${String(err)}`);
      });
    return () => {
      alive = false;
    };
  }, []);

  async function create() {
    const text = purpose.trim();
    if (creating || !text) return;
    setCreating(true);
    setCreateError(null);
    try {
      const res = await rest.createProject({ purpose: text });
      navigate(`/p/${res.project_id}`);
    } catch (err: unknown) {
      setCreateError(`项目创建失败：${String(err)}`);
      setCreating(false);
    }
  }

  return (
    <div className="min-h-full flex flex-col">
      {/* 顶栏：唯一常驻入口——密钥/登录 */}
      <div className="flex justify-end px-4 sm:px-6 py-4">
        <button
          type="button"
          onClick={() => setKeyOpen(true)}
          className="h-9 px-3.5 rounded-md hairline bg-card text-[12.5px] text-inksoft flex items-center gap-1.5 hover:bg-cardsunk transition cursor-pointer"
        >
          <IconLock className="w-4 h-4" />
          密钥 / 登录
        </button>
      </div>

      {/* 中央输入区：不等项目列表，挂载即可输入 */}
      <div className="flex-1 w-full max-w-[640px] mx-auto px-4 flex flex-col justify-center pb-16">
        <div className="text-center mb-7">
          <div className="text-[11px] tracking-[.2em] text-cinnabar mb-2 num">FIELDWORK COMPASS</div>
          <h1 className="font-serif text-[30px] sm:text-[34px] font-semibold leading-tight">田野罗盘</h1>
          <p className="text-[13px] text-inksoft mt-2">描述你的田野调研想法</p>
        </div>

        {/* 一句话输入（焦点惯例同 Composer：外壳 hairline → focus-within 朱砂描边） */}
        <div className="hairline rounded-lg bg-paper/60 p-3.5 flex items-end gap-3 focus-within:border-cinnabar/50 transition">
          <textarea
            ref={inputRef}
            rows={3}
            value={purpose}
            aria-label="调研想法"
            onChange={(e) => setPurpose(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void create();
              }
            }}
            placeholder="想研究什么？用一句话描述你的田野调研，例如田野点、时长与预算……"
            className="text-[13.5px] pointer-coarse:text-[16px] text-ink leading-relaxed flex-1 bg-transparent outline-none resize-none placeholder:text-inkfaint"
          />
          <button
            type="button"
            aria-label="创建项目"
            disabled={creating || !purpose.trim()}
            onClick={() => void create()}
            className="h-9 w-9 grid place-items-center rounded-md bg-cinnabar text-ontint shrink-0 hover:bg-tintpress transition disabled:opacity-50 disabled:pointer-events-none cursor-pointer"
          >
            <IconSend className="w-4 h-4" />
          </button>
        </div>

        {/* 示例 chips：点击填入完整示例并聚焦输入框 */}
        <div className="flex flex-wrap items-center gap-2 mt-3.5">
          <span className="text-[11.5px] text-inkfaint">试试：</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex.label}
              type="button"
              onClick={() => {
                setPurpose(ex.text);
                inputRef.current?.focus();
              }}
              className="chip transition bg-card hairline text-inksoft hover:bg-cardsunk cursor-pointer"
            >
              {ex.label}
            </button>
          ))}
        </div>

        {createError && (
          <div className="hairline rounded-lg bg-cinnabarwash/50 text-cinnabar text-[12.5px] px-4 py-3 mt-4">
            {createError}
          </div>
        )}

        {/* 已有项目：低调折叠入口（默认收起；无项目不渲染；失败如实提示） */}
        {projects !== null && projects.length > 0 ? (
          <div className="mt-12">
            <button
              type="button"
              aria-expanded={listOpen}
              onClick={() => setListOpen((v) => !v)}
              className="text-[12.5px] text-inksoft hover:text-ink transition inline-flex items-center gap-1.5 cursor-pointer"
            >
              继续上次 · 我的项目（{projects.length}）
              <IconCaret className={`caret w-3.5 h-3.5 ${listOpen ? "rotate-90" : ""}`} />
            </button>
            {listOpen && (
              <div className="mt-3 space-y-2">
                {projects.map((p) => (
                  <ProjectRow key={p.project_id} p={p} onOpen={() => navigate(`/p/${p.project_id}`)} />
                ))}
              </div>
            )}
          </div>
        ) : listError ? (
          <div className="mt-12 text-[12px] text-cinnabar leading-snug">{listError}</div>
        ) : null}
      </div>

      <KeyDialog open={keyOpen} onClose={() => setKeyOpen(false)} />
    </div>
  );
}
