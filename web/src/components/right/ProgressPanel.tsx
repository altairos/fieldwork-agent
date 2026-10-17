import { useEffect, useState } from "react";
import { Badge } from "../ui/Badge";
import { ProgressBar } from "../ui/ProgressBar";
import { IconSpark } from "../ui/icons";
import { DEFAULT_PHASES } from "../layout/LeftRail";
import { useProjectStore } from "../../stores/project";
import { useUiStore } from "../../stores/ui";
import { rest } from "../../api/rest";
import { strOf } from "../cards/data";
import type { ProjectSnapshot } from "../../types/models";

/**
 * ProgressPanel — 右栏「进度」面板（阶段状态投影）。
 *
 * 设计板同源：右栏进度面板 lines 619-635
 *   标题 line 620 `font-serif text-[15px] font-600 mb-3`「采集进度 · 阶段 A」
 *   条目 line 621-628 `ul space-y-2` + `li flex items-center gap-2.5 text-[13px]`
 *     + 圆形阶段标（bamboo 已完成 / cinnabar 进行中 / hairline 未解锁）→ 复用 Task 5 `Badge`
 *     + 文本 `text-inksoft`
 *   分隔 line 629 `rule my-4`；次级标题 line 630 `font-serif text-[15px] font-600 mb-2`
 *   四段进度条取 view-hub 项目卡 lines 330/344/357 → 复用 Task 5 `ProgressBar`
 *
 * 数据来源（同源红线②）：`project.phase`（phase_transition 归约）+ `snapshot.phase_history`
 * （REST 初始快照，web_host/api/projects.py L56）+ `ui.pendingSuggestion`。**不发任何请求**、
 * 不由 LLM 文本推断阶段。
 * 红线①：pendingSuggestion 只呈现「建议」，面板绝不自行 setActiveTab（切换动作只在右栏 toast 的「查看」）。
 */

/** 阶段 id → 「A · 研究定位」；未知 id 原样呈现（不猜）。 */
function phaseLabel(id: string | undefined): string {
  if (!id) return "（起始）";
  const meta = DEFAULT_PHASES.find((p) => p.id === id);
  return meta ? `${meta.letter} · ${meta.label}` : id;
}

export function ProgressPanel() {
  const phase = useProjectStore((s) => s.phase);
  const snapshot = useProjectStore((s) => s.snapshot);
  const suggestion = useUiStore((s) => s.pendingSuggestion);
  const history = snapshot?.phase_history ?? [];
  const current = DEFAULT_PHASES.findIndex((p) => p.id === phase);
  const meta = current >= 0 ? DEFAULT_PHASES[current] : null;

  return (
    <div>
      <div className="font-serif text-[15px] font-semibold mb-3">
        采集进度{meta ? ` · 阶段 ${meta.letter}` : ""}
      </div>

      <ProgressBar phase={Math.max(0, current)} segments={DEFAULT_PHASES.map((p) => p.letter)} />

      {/* 四阶段清单（设计板 lines 621-628） */}
      <ul className="space-y-2 mt-3">
        {DEFAULT_PHASES.map((p, i) => {
          const done = current >= 0 && i < current;
          const active = i === current;
          return (
            <li key={p.id} className="flex items-center gap-2.5 text-[13px]">
              <Badge phase={p.letter} active={active} done={done} />
              <span className={active ? "text-cinnabar font-medium" : "text-inksoft"}>
                {p.letter} · {p.label}
              </span>
              <span className="text-[10.5px] text-inkfaint ml-auto shrink-0">{p.desc}</span>
            </li>
          );
        })}
      </ul>

      {meta && (
        <p className="text-[12px] text-inksoft leading-relaxed mt-3">
          当前阶段说明：{meta.desc}。阶段推进由内核可验证条件判定，非模型自由发挥。
        </p>
      )}

      {/* 切换建议（不自动切；用户需在右栏顶部 toast 点「查看」） */}
      {suggestion && (
        <div className="hairline rounded-lg bg-card p-3 mt-3 flex items-start gap-2 text-[12px]">
          <IconSpark className="w-4 h-4 text-cinnabar shrink-0 mt-0.5" />
          <span className="text-inksoft flex-1 min-w-0">
            {suggestion.reason}。面板不会自动切换 —— 在本栏顶部的建议条点「查看」即可跳转。
          </span>
        </div>
      )}

      <div className="rule my-4" />

      {/* 阶段流转史（snapshot.phase_history，原样呈现时间戳与阶段 id） */}
      <div className="font-serif text-[15px] font-semibold mb-2">阶段流转</div>
      {history.length === 0 ? (
        <div className="text-[11.5px] text-inkfaint">尚无阶段流转记录</div>
      ) : (
        <ul className="space-y-2">
          {history.map((h, i) => (
            <li key={`${h.to ?? ""}-${i}`} className="flex items-baseline gap-2 text-[12.5px]">
              <span className="dot bg-bamboo shrink-0" aria-hidden="true" />
              <span className="text-inksoft min-w-0">
                {phaseLabel(h.from)} → {phaseLabel(h.to)}
              </span>
              {h.at && <span className="num text-[10.5px] text-inkfaint ml-auto shrink-0">{h.at}</span>}
            </li>
          ))}
        </ul>
      )}
      <div className="rule my-4" />
      <ResearchContextEditor snapshot={snapshot} />
    </div>
  );
}

/** 可编辑研究信息槽位（= ALLOWED_CONTEXT_FIELDS 的 11 个文本键；NA 布尔单独处理；
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

/** 研究信息编辑区：用户显式修正入口（审计 F03 修正入口右栏编辑；与回合内规则抽取互补）。 */
function ResearchContextEditor({ snapshot }: { snapshot: ProjectSnapshot | null }) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [na, setNa] = useState(false);
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
    setNa(ctx.interviews_not_applicable === true);
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
    if (na) {
      patch.interviews_not_applicable = true;
      if (naReason.trim() !== "") patch.interviews_na_reason = naReason.trim();
    }
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
      <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">研究信息（可修正）</div>
      {snapshot?.proposal_confirmation_stale === true && (
        <div className="text-[11px] text-ochre mb-2">
          方案确认已因研究信息变更失效，请在「方案」页重新确认。
        </div>
      )}
      <div className="grid grid-cols-2 gap-2">
        {EDIT_FIELDS.map((f) => (
          <label key={f.key} className="block min-w-0">
            <span className="text-[10px] text-inkfaint">{f.label}</span>
            <input
              aria-label={f.label}
              value={draft[f.key] ?? ""}
              onChange={(e) => {
                setDraft({ ...draft, [f.key]: e.target.value });
                setDirty(true);
              }}
              className="w-full mt-0.5 text-[12px] bg-cardsunk hairline rounded-md px-2 py-1 outline-none focus:border-cinnabar"
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
        <input
          aria-label="不适用原因"
          placeholder="不适用原因（可选，呈现于访谈对象面板）"
          value={naReason}
          onChange={(e) => { setNaReason(e.target.value); setDirty(true); }}
          className="w-full mt-1.5 text-[12px] bg-cardsunk hairline rounded-md px-2 py-1 outline-none focus:border-cinnabar"
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
