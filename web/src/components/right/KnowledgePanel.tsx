import { useEffect, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Chip } from "../ui/Chip";
import { EmptyState } from "../ui/EmptyState";
import { IconBook, IconCompass } from "../ui/icons";
import { asRecord, strOf } from "../cards/data";
import { rest, type KnowledgeEntry } from "../../api/rest";
import { useProjectStore } from "../../stores/project";
import { useToolsStore } from "../../stores/tools";

/**
 * KnowledgePanel — 右栏「知识库」面板（研究定位投影 + 学科纵深静态说明）。
 *
 * 设计板同源：右栏知识库面板 lines 661-669
 *   标题 line 663 `font-serif text-[15px] font-600 mb-3`
 *   条目卡 line 665 `hairline rounded-lg bg-card p-3` + 主行 `text-[12.5px] font-medium`
 *     + 副行 `text-[11px] text-inkfaint mt-1`（此处主行 = 槽位值，副行 = 槽位名，不臆造资料标题）
 *   学术纵深卡 lines 379-382 `hairline rounded-lg bg-card p-5` + `font-serif text-[15px] font-600 mb-2`
 *     + `text-[12px] text-inksoft leading-relaxed`（正文同设计板 line 381；R1 口径修订见 DEPTH_TEXT 注释）
 *   底部说明行 line 668 `hairline rounded-lg bg-paper/60 p-3 mt-3 flex items-center gap-2
 *     text-[12px] text-inkfaint`
 *
 * 同源红线②：只读 `snapshot.research_context`（REST 初始快照 = 内核 ResearchContext 槽位），
 * 不改写字段值（原样呈现内核抽取结果）；未填槽位如实计入「已填 N / 总数」，
 * 不猜、不补默认值。
 * 审计三 Fix 1c：槽位投影之外新增「已存条目」区——agent add_knowledge_entry 落盘材料经
 * `GET /projects/{id}/knowledge` 独立端点拉取（快照不含 knowledge，避免膨胀）；刷新时机 =
 * 挂载 + snapshot 引用变化 + add_knowledge_entry 工具完成（turn_done 不重拉快照，
 * 以工具完成信号兑底回合内新鲜度）。条目原样呈现，不加工、不裁剪。
 */

/** 槽位 → 中文行标（阶段 A 清单语汇取设计板 lines 622-627；轮 2 方案 B 加深挖槽）。 */
const SLOTS: { key: string; label: string }[] = [
  { key: "discipline", label: "学科背景" },
  { key: "topic", label: "研究主题" },
  { key: "research_questions", label: "研究问题" },
  { key: "site_status", label: "田野点确定度" },
  { key: "scale", label: "调研规模" },
  { key: "budget", label: "经费" },
  { key: "time_window", label: "时间窗口" },
  { key: "departure_city", label: "出发城市" },
  { key: "target_location", label: "目标地点" },
  { key: "travel_date", label: "出行日期" },
  { key: "purpose", label: "输出用途" },
];

/** purpose 枚举 → 中文（判词依据 kernel/guidance/extract.py L127-136）；未知值原样呈现。 */
const PURPOSE_LABEL: Record<string, string> = {
  kaiti: "开题报告",
  grant: "基金申报",
  course: "课程作业",
  global: "海外/留学申请",
};

/**
 * 学术纵深静态说明（与设计板 line 381 同源；2026-10-16 按审阅意见 R1 修订：去掉
 * 「自动生成…嵌入提案第三章」——访谈提纲是独立文档（compose_interview_guide 产出），
 * 仅被方案的方法/伦理章节引用，不存在自动嵌入机制。导出供 ProjectHub 侧栏复用同文案）。
 */
export const DEPTH_TEXT =
  "内置 10+ 类访谈对象类型库（村落长老/宗族头人/宗教人士/基层干部/返乡务工者/非遗传承人…），每类含接近策略、注意事项、建议提问域与伦理提示，可据此起草半结构式访谈提纲，供方案的方法与伦理章节引用。";

/** 条目正文的 Markdown 排版（P1）：面板小字号版，先例 ProposalViewer.COMPONENTS。
 *  单换行 → 硬换行用 content.split("\n").join("  \n") 预处理（无新依赖）。 */
const ENTRY_COMPONENTS: Components = {
  p: ({ children }) => <p className="text-[11.5px] leading-relaxed text-inksoft">{children}</p>,
  strong: ({ children }) => <strong className="text-ink font-semibold">{children}</strong>,
  ul: ({ children }) => (
    <ul className="list-disc pl-4 my-1 space-y-0.5 text-[11.5px] leading-relaxed text-inksoft">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal pl-4 my-1 space-y-0.5 text-[11.5px] leading-relaxed text-inksoft">{children}</ol>
  ),
  code: ({ children }) => <code className="num text-indigo text-[.92em]">{children}</code>,
};

/** 单条知识条目（P1）：默认折叠只显标题；展开后按 Markdown 渲染正文；可就地编辑保存。 */
function KnowledgeItem({
  entry, projectId, onSaved,
}: {
  entry: KnowledgeEntry;
  projectId: string;
  onSaved: (updated: KnowledgeEntry) => void;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(entry.title);
  const [content, setContent] = useState(entry.content);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(false);

  const save = async () => {
    setSaving(true);
    setSaveError(false);
    try {
      const updated = await rest.updateKnowledge(projectId, entry.id, { title, content });
      onSaved(updated);
      setEditing(false);
    } catch {
      setSaveError(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="hairline rounded-lg bg-card p-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 text-left"
      >
        <span
          className={`num text-[10px] text-inkfaint shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
        >
          ▸
        </span>
        <span className="text-[12.5px] font-medium text-ink break-words flex-1">{entry.title}</span>
        <span className="text-[11px] text-inkfaint shrink-0">{entry.category ?? "general"}</span>
      </button>
      {open && !editing && (
        <div className="mt-2">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={ENTRY_COMPONENTS}>
            {entry.content.split("\n").join("  \n")}
          </ReactMarkdown>
          <button
            type="button"
            onClick={() => { setEditing(true); setSaveError(false); }}
            className="text-[11px] text-indigo hover:underline"
          >
            编辑
          </button>
        </div>
      )}
      {open && editing && (
        <div className="mt-2 space-y-2">
          <input
            aria-label="标题"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full text-[12px] bg-cardsunk hairline rounded-md px-2 py-1 outline-none focus:border-cinnabar"
          />
          <textarea
            aria-label="内容"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            rows={6}
            className="w-full text-[11.5px] leading-relaxed bg-cardsunk hairline rounded-md px-2 py-1 outline-none focus:border-cinnabar resize-y"
          />
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={save}
              disabled={saving}
              className="text-[11px] px-2 py-1 rounded-md bg-ink text-paper disabled:opacity-50"
            >
              {saving ? "保存中…" : "保存"}
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setTitle(entry.title);
                setContent(entry.content);
              }}
              className="text-[11px] text-inkfaint hover:underline"
            >
              取消
            </button>
            {saveError && <span className="text-[11px] text-cinnabar">保存失败，稍后重试</span>}
          </div>
        </div>
      )}
    </div>
  );
}

export function KnowledgePanel() {
  const projectId = useProjectStore((s) => s.projectId);
  const snapshot = useProjectStore((s) => s.snapshot);
  const ctx = asRecord(snapshot?.research_context);
  const hasContext = snapshot !== null && Object.keys(ctx).length > 0;

  // 回合内新鲜度：add_knowledge_entry 落盘完成（done/failed）的调用序列变化即重拉；
  // running 不算（落盘未完成拉了也白拉）。字符串 stamp 保证 zustand selector 稳定引用。
  const knowledgeStamp = useToolsStore((s) =>
    s.order
      .filter((cid) => {
        const c = s.byCallId[cid];
        return c?.tool === "add_knowledge_entry" && c.status !== "running";
      })
      .map((cid) => `${cid}:${s.byCallId[cid]?.status}`)
      .join(","));
  const [entries, setEntries] = useState<KnowledgeEntry[] | null>(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    rest
      .listKnowledge(projectId)
      .then((items) => {
        if (alive) {
          setEntries(items);
          setLoadError(false);
        }
      })
      .catch(() => {
        if (alive) setLoadError(true);
      });
    return () => {
      alive = false;
    };
  }, [projectId, snapshot, knowledgeStamp]);

  const slots = SLOTS.map((slot) => {
    const raw = strOf(ctx[slot.key]);
    const value = slot.key === "purpose" && raw ? (PURPOSE_LABEL[raw] ?? raw) : raw;
    return { ...slot, value };
  });
  const filledCount = slots.filter((s) => s.value !== "").length;
  // 野生字段（内核 extras 收口）：只呈现标量，原样文本，不解释、不加工
  const extras = Object.entries(asRecord(ctx.extras)).filter(
    ([, v]) => typeof v === "string" || typeof v === "number" || typeof v === "boolean",
  );

  return (
    <div>
      <div className="font-serif text-[15px] font-semibold mb-3">研究定位 · 知识库</div>

      {!hasContext || filledCount === 0 ? (
        <EmptyState
          icon={<IconCompass className="w-4 h-4" />}
          title="尚未取得研究定位"
          hint="阶段 A 的访谈会逐槽位补齐（学科·问题·规模·经费·时间窗）；本面板只做投影，不改写内核字段"
        />
      ) : (
        <>
          <div className="space-y-2">
            {slots.map((s) =>
              s.value === "" ? null : (
                <div key={s.key} className="hairline rounded-lg bg-card p-3">
                  <div className="text-[12.5px] font-medium text-ink break-words">{s.value}</div>
                  <div className="text-[11px] text-inkfaint mt-1">{s.label}</div>
                </div>
              ),
            )}
          </div>
          <div className="num text-[11px] text-inkfaint mt-2">
            {`已填 ${filledCount} / ${SLOTS.length} 槽位`}
          </div>

          {extras.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-3">
              {extras.map(([key, value]) => (
                <Chip key={key} tone="neutral">
                  {`${key} ${String(value)}`}
                </Chip>
              ))}
            </div>
          )}
        </>
      )}

      <div className="rule my-4" />

      {/* 已存条目（审计三 Fix 1c + P1）：默认折叠，展开看 Markdown 正文，可就地编辑 */}
      <div className="mb-4">
        <div className="num text-[11px] text-inkfaint mb-2">
          {`已存条目 · ${entries?.length ?? "…"}`}
        </div>
        {loadError ? (
          <div className="text-[12px] text-inkfaint">知识条目加载失败，稍后重试</div>
        ) : entries === null ? null : entries.length === 0 ? (
          <div className="text-[12px] text-inkfaint">尚未存入知识条目（agent 对话中存入的材料会出现在这里）</div>
        ) : (
          <div className="space-y-2">
            {entries.map((e) => (
              <KnowledgeItem
                key={e.id}
                entry={e}
                projectId={projectId ?? ""}
                onSaved={(updated) =>
                  setEntries((prev) =>
                    prev?.map((it) => (it.id === updated.id ? updated : it)) ?? prev)}
              />
            ))}
          </div>
        )}
      </div>

      {/* 学术纵深（设计板 lines 379-382 静态说明，非工具数据，不与同源红线冲突） */}
      <div className="hairline rounded-lg bg-card p-4">
        <div className="font-serif text-[15px] font-semibold mb-2 flex items-center gap-2">
          <IconBook className="w-4 h-4 text-cinnabar" />
          学术纵深
        </div>
        <p className="text-[12px] text-inksoft leading-relaxed">{DEPTH_TEXT}</p>
      </div>

      <div className="hairline rounded-lg bg-paper/60 p-3 mt-3 flex items-center gap-2 text-[12px] text-inkfaint">
        <IconCompass className="w-4 h-4 shrink-0" />
        本面板实时同步系统状态：数值与文本一律来自内核快照与工具返回，不经模型转述。
      </div>
    </div>
  );
}
