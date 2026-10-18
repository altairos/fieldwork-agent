import { useEffect, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { rest, type KnowledgeEntry } from "../../api/rest";
import { useProjectStore } from "../../stores/project";
import { useToolsStore } from "../../stores/tools";

/**
 * KnowledgePanel — 右栏「知识库」面板（已存条目）。
 *
 * 设计板同源：右栏知识库面板 lines 661-669
 *   条目卡 line 665 `hairline rounded-lg bg-card p-3` + 主行 `text-[12.5px] font-medium`
 *   （2026-10-18 精简：删去面板标题「研究定位 · 知识库」与「学术纵深」静态卡——
 *     Tab 已标知识库、面板无重复标题的必要；实时口径声明栏上提为 RightDock 常驻 footer，
 *     五个 Tab 一致呈现。）
 *
 * （2026-10-18 槽位投影整体删除：槽位卡 / 「已填 N / 11 槽位」计数 / extras chips 移除——
 *   槽位编辑入口收敛到「基本信息」面板（随时可改），知识库只呈现 agent 存入的材料条目，
 *   不再重复投影研究定位。）
 * 审计三 Fix 1c：「已存条目」区——agent add_knowledge_entry 落盘材料经
 * `GET /projects/{id}/knowledge` 独立端点拉取（快照不含 knowledge，避免膨胀）；刷新时机 =
 * 挂载 + snapshot 引用变化 + add_knowledge_entry 工具完成（turn_done 不重拉快照，
 * 以工具完成信号兑底回合内新鲜度）。条目原样呈现，不加工、不裁剪。
 */

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

  return (
    <div>
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
    </div>
  );
}
