import { Badge } from "../ui/Badge";
import { ProgressBar } from "../ui/ProgressBar";
import { IconSpark } from "../ui/icons";
import { DEFAULT_PHASES } from "../layout/LeftRail";
import { useProjectStore } from "../../stores/project";
import { useUiStore } from "../../stores/ui";

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
    </div>
  );
}
