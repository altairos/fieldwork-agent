import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { rest } from "../api/rest";
import { DEFAULT_PHASES } from "../components/layout/LeftRail";
import { Button } from "../components/ui/Button";
import { Chip, type ChipTone } from "../components/ui/Chip";
import { EmptyState } from "../components/ui/EmptyState";
import { ProgressBar } from "../components/ui/ProgressBar";
import {
  IconBook,
  IconClock,
  IconCompass,
  IconPin,
  IconPlus,
  IconSend,
  IconSpark,
  IconUsers,
} from "../components/ui/icons";
import type { ProjectSummary } from "../types/models";

/**
 * ProjectHub — 路由 `/`：项目集列表 + 新建项目卡。
 * 设计板同源：view-hub（lines 276-385）：
 *   标题区（280-286）：kicker「PROJECT HUB · 项目集」+ h1「你的田野调研项目」+ 新建项目主按钮
 *   新建卡（288-313）：学科倾向 chips（292-300，单选高亮 cinnabarwash）+
 *     快速模板 chips（301-307，单选高亮 indigowash）+ 一句话描述 textarea + 发送按钮（308-312）
 *   项目卡网格（315-365）：名+阶段chip+学科/规模/预算+田野点/访谈对象计数+更新时间+四段进度条（330）
 *     + 末位「新建空白项目」虚线卡（360-364）
 *   侧栏（369-383）：四阶段访谈说明 + 学术纵深说明
 *   空列表 → EmptyState「还没有项目，从一个想法开始」（此时新建卡的标题位由 EmptyState 承担，
 *     避免同文案双元素；列表加载完成前不渲染新建卡，空态判定以服务端结果为准）
 */

/** 学科倾向（设计板 294-299） */
const DISCIPLINES = ["人类学", "社会学", "民族学", "公共卫生", "教育学", "建筑/乡建"];
/** 快速模板（设计板 303-306） */
const TEMPLATES = ["村落宗族组织研究", "非遗传承调查", "乡村教育民族志", "基层医疗访谈"];

/** 阶段 → chip tone（设计板：C 行程规划 cinnabar(322)、B 田野点画像 indigo(336)、D 完成态 bamboo(350)、A 无样例取 neutral） */
const PHASE_TONE: Record<string, ChipTone> = {
  research_positioning: "neutral",
  field_site_profiles: "indigo",
  itinerary_planning: "cinnabar",
  proposal_generation: "bamboo",
};
/** tone → 状态点色（Tailwind 需静态类名，不可拼接） */
const DOT_BG: Record<ChipTone, string> = {
  cinnabar: "bg-cinnabar",
  indigo: "bg-indigo",
  bamboo: "bg-bamboo",
  ochre: "bg-ochre",
  neutral: "bg-inkfaint",
};

/** 更新时间 → 人读相对时间（设计板 328「2 小时前」/342「昨天」/355「3 天前」） */
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

/** 项目卡（设计板 319-331） */
function ProjectCard({ p, onOpen }: { p: ProjectSummary; onOpen: () => void }) {
  const idx = DEFAULT_PHASES.findIndex((x) => x.id === p.phase);
  const meta = idx >= 0 ? DEFAULT_PHASES[idx] : null;
  const tone = PHASE_TONE[p.phase] ?? "neutral";
  const ctx = p.research_context ?? {};
  const metaLine = [ctx.discipline, ctx.scale, ctx.budget ? `预算 ${ctx.budget}` : ""]
    .filter(Boolean)
    .join(" · ");
  const counts = p.counts ?? { sites: 0, informants: 0, proposals: 0 };
  return (
    <button
      type="button"
      onClick={onOpen}
      className="hairline rounded-lg bg-card shadow-card p-4 block w-full text-left hover:border-cinnabar/40 hover:shadow-pop transition group"
    >
      <div className="flex items-start justify-between mb-2.5">
        <div className="font-serif text-[16px] font-semibold leading-snug group-hover:text-cinnabar transition">
          {ctx.topic || p.project_id}
        </div>
        {meta && (
          <Chip tone={tone} className="shrink-0 ml-2">
            <span className={`dot ${DOT_BG[tone]}`} aria-hidden="true" />
            {meta.letter} {meta.label}
          </Chip>
        )}
      </div>
      {metaLine && <div className="text-[12px] text-inksoft mb-3">{metaLine}</div>}
      <div className="flex items-center gap-4 text-[11.5px] text-inkfaint">
        <span className="flex items-center gap-1">
          <IconPin className="w-3.5 h-3.5" />
          <span className="num">{counts.sites}</span> 田野点
        </span>
        <span className="flex items-center gap-1">
          <IconUsers className="w-3.5 h-3.5" />
          <span className="num">{counts.informants}</span> 访谈对象
        </span>
        {counts.proposals > 0 && (
          <span className="flex items-center gap-1">
            <IconBook className="w-3.5 h-3.5" />
            <span className="num">{counts.proposals}</span> 提案
          </span>
        )}
        <span className="ml-auto flex items-center gap-1">
          <IconClock className="w-3.5 h-3.5" />
          {fmtRelative(p.updated_at ?? p.created_at)}
        </span>
      </div>
      <ProgressBar phase={Math.max(idx, 0)} segments={["A", "B", "C", "D"]} className="mt-3" />
    </button>
  );
}

export function ProjectHub() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [discipline, setDiscipline] = useState("");
  const [template, setTemplate] = useState("");
  const [purpose, setPurpose] = useState("");
  const [creating, setCreating] = useState(false);
  const descRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let alive = true;
    rest
      .listProjects()
      .then((list) => {
        if (alive) setProjects(list);
      })
      .catch((err: unknown) => {
        if (alive) setError(`项目列表加载失败：${String(err)}`);
      });
    return () => {
      alive = false;
    };
  }, []);

  async function create(body: { discipline?: string; topic?: string; purpose?: string }) {
    setCreating(true);
    try {
      const res = await rest.createProject(body);
      // 跨项目 reset 由 Workspace 挂载 effect 按 projectId 变化统一处理（App.tsx）
      navigate(`/p/${res.project_id}`);
    } catch (err: unknown) {
      setError(`项目创建失败：${String(err)}`);
      setCreating(false);
    }
  }

  function sendFromForm() {
    if (creating || !(purpose.trim() || template)) return;
    void create({
      discipline: discipline || undefined,
      topic: template || undefined,
      purpose: purpose.trim() || undefined,
    });
  }

  const loading = projects === null && !error;
  const empty = projects !== null && projects.length === 0;
  // 加载完成前不渲染新建卡（空态判定以服务端结果为准）；失败时仍给出创建入口
  const showForm = projects !== null || Boolean(error);

  return (
    <div className="min-h-full">
      <div className="max-w-6xl mx-auto px-6 py-8">
        <div className="grid lg:grid-cols-[1fr_320px] gap-6">
          {/* 主区 */}
          <div>
            {/* 标题区（设计板 280-286） */}
            <div className="flex items-end justify-between mb-5">
              <div>
                <div className="text-[11px] tracking-[.2em] text-cinnabar mb-1.5 num">
                  PROJECT HUB · 项目集
                </div>
                <h1 className="font-serif text-[26px] font-semibold">你的田野调研项目</h1>
              </div>
              <Button
                variant="primary"
                className="h-10 shadow-card"
                onClick={() => {
                  descRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
                  descRef.current?.focus();
                }}
              >
                <IconPlus className="w-4 h-4" />
                新建项目
              </Button>
            </div>

            {error && (
              <div className="hairline rounded-lg bg-cinnabarwash/50 text-cinnabar text-[12.5px] px-4 py-3 mb-4">
                {error}
              </div>
            )}

            {loading && (
              <div className="hairline rounded-lg bg-card/60 p-5 mb-6 text-[12.5px] text-inkfaint text-center">
                项目列表加载中…
              </div>
            )}

            {/* 新建卡（设计板 288-313；空列表时标题位让给 EmptyState，避免同文案双元素） */}
            {showForm && (
              <div className="hairline rounded-lg bg-card shadow-card p-5 mb-6">
                {empty ? (
                  <EmptyState
                    icon={<IconSpark className="w-4 h-4" />}
                    title="还没有项目，从一个想法开始"
                    hint="选择学科倾向与快速模板，或直接输入一句话描述你的田野计划"
                  />
                ) : (
                  <>
                    <div className="font-serif text-[16px] font-semibold mb-1">从一个想法开始</div>
                    <p className="text-[12.5px] text-inkfaint mb-4">
                      选择学科倾向与快速模板，或直接一句话描述你的田野计划——罗盘会通过四阶段访谈把它补全为完整研究方案。
                    </p>
                  </>
                )}

                {/* 学科倾向 chips（设计板 292-300，单选高亮） */}
                <div className="flex flex-wrap gap-2 mb-4">
                  <span className="text-[11.5px] text-inksoft mr-1 self-center">学科倾向</span>
                  {DISCIPLINES.map((d) => (
                    <button
                      key={d}
                      type="button"
                      aria-pressed={discipline === d}
                      onClick={() => setDiscipline(discipline === d ? "" : d)}
                      className={`chip transition ${
                        discipline === d
                          ? "bg-cinnabarwash text-cinnabar border-cinnabar/25"
                          : "bg-card hairline text-inksoft hover:bg-cardsunk"
                      }`}
                    >
                      {d}
                    </button>
                  ))}
                </div>

                {/* 快速模板 chips（设计板 301-307，单选高亮 indigo） */}
                <div className="flex flex-wrap gap-2 mb-4">
                  <span className="text-[11.5px] text-inksoft mr-1 self-center">快速模板</span>
                  {TEMPLATES.map((t) => (
                    <button
                      key={t}
                      type="button"
                      aria-pressed={template === t}
                      onClick={() => setTemplate(template === t ? "" : t)}
                      className={`chip transition ${
                        template === t
                          ? "bg-indigowash text-indigo border-indigo/20"
                          : "bg-card hairline text-inksoft hover:bg-cardsunk"
                      }`}
                    >
                      {t}
                    </button>
                  ))}
                </div>

                {/* 一句话描述（设计板 308-312；#F7EFE6/#96382a 为设计板 token 外用值，line 311 同源） */}
                <div className="hairline rounded-md bg-paper/60 p-3 flex items-start gap-3">
                  <IconSpark className="w-5 h-5 text-inkfaint mt-0.5 shrink-0" />
                  <textarea
                    ref={descRef}
                    rows={2}
                    value={purpose}
                    onChange={(e) => setPurpose(e.target.value)}
                    placeholder="研究西南少数民族村落的宗族组织变迁，单人驻村三个月，预算两万元，时间窗口明年 6–9 月……"
                    className="text-[13px] text-ink leading-relaxed flex-1 bg-transparent outline-none resize-none placeholder:text-inkfaint"
                  />
                  <button
                    type="button"
                    aria-label="创建项目"
                    disabled={creating || !(purpose.trim() || template)}
                    onClick={sendFromForm}
                    className="h-8 w-8 grid place-items-center rounded-md bg-cinnabar text-[#F7EFE6] shrink-0 hover:bg-[#96382a] transition disabled:opacity-50 disabled:pointer-events-none"
                  >
                    <IconSend className="w-4 h-4" />
                  </button>
                </div>
              </div>
            )}

            {/* 项目列表（设计板 315-365） */}
            {projects !== null && projects.length > 0 && (
              <>
                <div className="text-[12px] text-inkfaint tracking-wide mb-3">
                  进行中 / 已完成 · <span className="num">{projects.length}</span> 个项目
                </div>
                <div className="grid sm:grid-cols-2 gap-4">
                  {projects.map((p) => (
                    <ProjectCard key={p.project_id} p={p} onOpen={() => navigate(`/p/${p.project_id}`)} />
                  ))}
                  {/* 新建空白项目（设计板 360-364） */}
                  <button
                    type="button"
                    disabled={creating}
                    onClick={() => void create({})}
                    className="hairline border-dashed rounded-lg bg-card/60 p-4 block hover:bg-card transition w-full flex flex-col items-center justify-center text-center min-h-[132px] disabled:opacity-50"
                  >
                    <span className="w-9 h-9 grid place-items-center rounded-full hairline text-inkfaint mb-2">
                      <IconPlus className="w-4 h-4" />
                    </span>
                    <div className="text-[13px] text-inksoft">新建空白项目</div>
                    <div className="text-[11px] text-inkfaint mt-0.5">从阶段 A 研究定位开始</div>
                  </button>
                </div>
              </>
            )}
          </div>

          {/* 侧栏：说明（设计板 369-383） */}
          <aside className="space-y-4">
            <div className="hairline rounded-lg bg-paperdeep p-5">
              <div className="font-serif text-[15px] font-semibold mb-3 flex items-center gap-2">
                <IconCompass className="w-4 h-4 text-cinnabar" />
                四阶段访谈
              </div>
              <ol className="space-y-2.5 text-[12.5px]">
                {DEFAULT_PHASES.map((ph) => (
                  <li key={ph.id} className="flex gap-2.5">
                    <span className="num text-cinnabar font-semibold">{ph.letter}</span>
                    <span>
                      <b>{ph.label}</b>
                      <div className="text-inkfaint text-[11.5px]">{ph.desc}</div>
                    </span>
                  </li>
                ))}
              </ol>
            </div>
            <div className="hairline rounded-lg bg-card p-5">
              <div className="font-serif text-[15px] font-semibold mb-2">学术纵深</div>
              <p className="text-[12px] text-inksoft leading-relaxed">
                内置 10+ 类访谈对象类型库（村落长老/宗族头人/宗教人士/基层干部/返乡务工者/非遗传承人…），每类含接近策略、注意事项、建议提问域与伦理提示，可自动生成半结构式访谈提纲并嵌入提案第三章。
              </p>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
