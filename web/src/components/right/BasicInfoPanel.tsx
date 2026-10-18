import { type TextareaHTMLAttributes, useEffect, useRef, useState } from "react";
import { useProjectStore } from "../../stores/project";
import { rest } from "../../api/rest";
import { strOf } from "../cards/data";
import type { ProjectSnapshot } from "../../types/models";

/**
 * BasicInfoPanel — 右栏「基本信息」面板（研究信息随时编辑）。
 *
 * 原 ProgressPanel 的采集进度清单/阶段流转史已删除（左栏「方案规划阶段」功能对等，
 * 右栏不再重复投影）；切换建议 toast 由 RightDock 容器独立呈现，不受影响。
 * 仅保留审计 F03 的用户显式修正入口（右栏编辑，与回合内规则抽取互补）。
 */
export function BasicInfoPanel() {
  const snapshot = useProjectStore((s) => s.snapshot);
  return <ResearchContextEditor snapshot={snapshot} />;
}

/** 可编辑基本信息槽位（= ALLOWED_CONTEXT_FIELDS 的 11 个文本键；NA 布尔单独处理；
 *  轮 2 方案 B 加 research_questions 深挖槽）。 */
const EDIT_FIELDS: { key: string; label: string }[] = [
  { key: "discipline", label: "学科背景" },
  { key: "topic", label: "研究主题" },
  { key: "research_questions", label: "研究问题" },
  { key: "site_status", label: "身份/在场方式" },
  { key: "scale", label: "规模" },
  { key: "budget", label: "预算" },
  { key: "time_window", label: "时间窗" },
  { key: "departure_city", label: "出发城市" },
  { key: "target_location", label: "目标地点" },
  { key: "travel_date", label: "出行日期" },
  { key: "purpose", label: "输出用途" },
];

/** 基本信息编辑区：用户显式修正入口（审计 F03 修正入口右栏编辑；与回合内规则抽取互补）。 */
function ResearchContextEditor({ snapshot }: { snapshot: ProjectSnapshot | null }) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [na, setNa] = useState(false);
  // N03：NA 基线（来自快照的已存值）——仅当用户改动勾选时才上行该键，
  // 且取消勾选时明确发 false 以撤销已保存的 NA（旧 if(na) 分支漏发 false）。
  const [naBaseline, setNaBaseline] = useState(false);
  const [naReason, setNaReason] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedHint, setSavedHint] = useState<string | null>(null);

  // 快照异步到达/外部刷新时同步草稿；用户编辑中（dirty）不覆盖，保存成功后复位
  useEffect(() => {
    if (dirty) return;
    const ctx = (snapshot?.research_context ?? {}) as Record<string, unknown>;
    const next: Record<string, string> = {};
    for (const f of EDIT_FIELDS) next[f.key] = strOf(ctx[f.key]);
    setDraft(next);
    const naNow = ctx.interviews_not_applicable === true;
    setNa(naNow);
    setNaBaseline(naNow);
    setNaReason(strOf(ctx.interviews_na_reason));
  }, [snapshot, dirty]);

  const save = async () => {
    const pid = snapshot?.project_id;
    if (!pid || saving) return;
    const patch: Record<string, string | boolean> = {};
    for (const f of EDIT_FIELDS) {
      const v = (draft[f.key] ?? "").trim();
      if (v !== "") patch[f.key] = v; // 空字段不上行（PUT 空白字段无意义且会被后端清空语义误读）
    }
    // N03：勾选状态变化时明确上行 true/false（含取消勾选发 false 撤销已存 NA）
    if (na !== naBaseline) patch.interviews_not_applicable = na;
    if (na && naReason.trim() !== "") patch.interviews_na_reason = naReason.trim();
    if (Object.keys(patch).length === 0) {
      setError("未填写任何要修正的字段（空字段不覆盖已有值）");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await rest.updateResearchContext(pid, patch);
      const snap2 = await rest.getProject(pid);
      useProjectStore.getState().loadSnapshot(snap2);
      setDirty(false);
      setSavedHint(snap2.proposal_confirmation_stale
        ? "已保存；此前的方案确认已因研究信息变更失效"
        : "已保存");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="hairline rounded-lg bg-card p-3">
      <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">基本信息（可随时编辑）</div>
      {snapshot?.proposal_confirmation_stale === true && (
        <div className="text-[11px] text-ochre mb-2">
          方案确认已因研究信息变更失效，请在「方案」页重新确认。
        </div>
      )}
      {/* 单栏（2026-10-18）：双栏改单栏，每槽位占整行；textarea 高度随文本
          与面板宽度自适应（AutoGrowTextarea 监听 value 变化 + ResizeObserver） */}
      <div className="grid grid-cols-1 gap-2">
        {EDIT_FIELDS.map((f) => (
          <label key={f.key} className="flex flex-col min-w-0">
            <span className="text-[10px] text-inkfaint">{f.label}</span>
            <AutoGrowTextarea
              aria-label={f.label}
              value={draft[f.key] ?? ""}
              onChange={(e) => {
                setDraft({ ...draft, [f.key]: e.target.value });
                setDirty(true);
              }}
              className="grow shrink-0 w-full mt-0.5 text-[12px] leading-relaxed bg-cardsunk hairline rounded-md px-2 py-1 outline-none focus:border-cinnabar resize-none overflow-hidden"
            />
          </label>
        ))}
      </div>
      <label className="flex items-center gap-2 mt-2 text-[12px] text-inksoft">
        <input
          type="checkbox"
          checked={na}
          onChange={(e) => { setNa(e.target.checked); setDirty(true); }}
        />
        本研究不适用访谈对象（非访谈型研究）
      </label>
      {na && (
        <AutoGrowTextarea
          aria-label="不适用原因"
          placeholder="不适用原因（可选，呈现于访谈对象面板）"
          value={naReason}
          onChange={(e) => { setNaReason(e.target.value); setDirty(true); }}
          className="w-full mt-1.5 text-[12px] leading-relaxed bg-cardsunk hairline rounded-md px-2 py-1 outline-none focus:border-cinnabar resize-none overflow-hidden"
        />
      )}
      <div className="flex items-center gap-2 mt-2">
        <button
          type="button"
          disabled={saving}
          onClick={save}
          className="text-[11.5px] text-cinnabar hairline rounded-md px-2.5 py-1 hover:bg-cardsunk disabled:opacity-50"
        >
          {saving ? "保存中…" : "保存修正"}
        </button>
        {savedHint && !error && <span className="text-[11px] text-bamboo">{savedHint}</span>}
        {error && <span className="text-[11px] text-ochre">{error}</span>}
      </div>
    </div>
  );
}

/** P4 自动增高多行输入：单行 input 截断长文本（且 DOM value 会剥离换行），
 *  改为 textarea 后内容随行数/软换行即时撑高，完整可见（无需手动拉把手）。
 *  高度同步在浏览器读 scrollHeight；除 [value] 外，宽度变化（右栏拖宽/
 *  收窄 → 软换行重排）经 ResizeObserver 重算，面板背景随之自适应。
 *  jsdom 无布局（scrollHeight 恒 0）也无 ResizeObserver，守卫跳过，
 *  高度退化无效，但 rows/换行行为不变，测试语义不受影响。 */
function AutoGrowTextarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const { value, ...rest } = props;
  const resize = () => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  };
  useEffect(resize, [value]);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return <textarea ref={ref} rows={1} value={value} {...rest} />;
}
