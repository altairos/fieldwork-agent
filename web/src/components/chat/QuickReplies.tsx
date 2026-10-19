import { useState } from "react";
import type { QuickReplyGroup } from "../../types/events";

/**
 * QuickReplies — 快捷作答 chips（多组带标签）。
 *
 * 数据源：conversation store `quickReplyGroups`（turn_done 事件 / hydrate 恢复），
 * 由 Composer 在输入壳上方渲染。每组对应助手当轮问的一个可枚举问题：
 * - **单组且无标签**：保留「点一下即发送」快速路径（onSend 直调 api.send），
 *   与历史单问体验一致；
 * - **多组或带标签**：逐组渲染一行（带 label 小标题）chips，组内单选、可点已选取消；
 *   底部「发送答案」把各选中项拼成 `标签：选项`（无标签则仅选项）多行一条消息发出，
 *   从而一次回答多个问题（问题①：不再只覆盖其中一个）。
 * 「其他（在下方输入）」始终聚焦输入框转入打字。
 * B1 样式：选项按钮走朱砂主色（浅洗底 + 主色描边，hover/选中实心），
 * 「其他」保持 hairline 次级样式区分主次。
 * 圆角/高度：不用 .chip 的 9999px 大圆角与 py-1.5 高桩，改 rounded-md + py-1 紧凑档，
 * 与页面按钮（Header tab / Timeline 按钮）同族（用户反馈：圆角过大、高度过高）。
 */
const CHIP = "inline-flex items-center rounded-md px-2.5 py-1 text-[12px] leading-[18px] border cursor-pointer transition";
const CHIP_IDLE =
  "bg-cinnabarwash text-cinnabar border-cinnabar/50 hover:bg-cinnabar hover:text-ontint hover:border-cinnabar";
const CHIP_ACTIVE = "bg-cinnabar text-ontint border-cinnabar";
const CHIP_SECONDARY =
  "inline-flex items-center rounded-md px-2.5 py-1 text-[12px] leading-[18px] border hairline bg-card text-inksoft hover:bg-cardsunk hover:text-ink cursor-pointer transition";

export function QuickReplies({
  groups,
  onSend,
  onCustom,
}: {
  groups: QuickReplyGroup[];
  onSend: (text: string) => void;
  onCustom: () => void;
}) {
  // 各组当前选中项（下标对齐 groups；"" = 未选）。组件随 groups 清空而卸载，状态自然复位。
  const [picked, setPicked] = useState<string[]>([]);
  if (!groups.length) return null;

  const fastPath = groups.length === 1 && !groups[0].label;

  if (fastPath) {
    return (
      <div className="flex flex-wrap items-center gap-1.5" data-testid="quick-replies">
        {groups[0].options.map((opt) => (
          <button
            key={opt}
            type="button"
            className={`${CHIP} ${CHIP_IDLE}`}
            onClick={() => onSend(opt)}
          >
            {opt}
          </button>
        ))}
        <button type="button" className={CHIP_SECONDARY} onClick={onCustom}>
          其他（在下方输入）
        </button>
      </div>
    );
  }

  const toggle = (gi: number, opt: string) =>
    setPicked((prev) => {
      const next = prev.slice();
      next[gi] = next[gi] === opt ? "" : opt;   // 再点已选 = 取消
      return next;
    });

  const composed = groups
    .map((g, gi) => ({ label: g.label, sel: picked[gi] }))
    .filter((x) => x.sel)
    .map((x) => (x.label ? `${x.label}：${x.sel}` : x.sel))
    .join("\n");

  return (
    <div className="space-y-2" data-testid="quick-replies">
      {groups.map((g, gi) => (
        <div
          key={gi}
          className="flex flex-wrap items-center gap-1.5"
          data-testid="quick-reply-group"
        >
          {g.label && (
            <span className="text-[11px] text-inksoft shrink-0 mr-0.5">{g.label}</span>
          )}
          {g.options.map((opt) => {
            const active = picked[gi] === opt;
            return (
              <button
                key={opt}
                type="button"
                aria-pressed={active}
                className={`${CHIP} ${active ? CHIP_ACTIVE : CHIP_IDLE}`}
                onClick={() => toggle(gi, opt)}
              >
                {opt}
              </button>
            );
          })}
        </div>
      ))}
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          data-testid="quick-reply-send"
          disabled={!composed}
          className="inline-flex items-center rounded-md px-2.5 py-1 text-[12px] leading-[18px] border bg-cinnabar text-ontint border-cinnabar cursor-pointer transition disabled:opacity-40 disabled:cursor-not-allowed"
          onClick={() => composed && onSend(composed)}
        >
          发送答案
        </button>
        <button type="button" className={CHIP_SECONDARY} onClick={onCustom}>
          其他（在下方输入）
        </button>
      </div>
    </div>
  );
}
