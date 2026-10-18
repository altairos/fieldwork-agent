/**
 * QuickReplies — 快捷作答 chips（B 节问题 4）。
 *
 * 数据源：conversation store `quickReplies`（turn_done 事件 / hydrate 恢复），
 * 由 Composer 在输入壳上方渲染；用户点选即发送（onPick 直调 api.send），
 * 「自定义」聚焦输入框转入打字。
 * 样式与 Timeline EmptyState 示例 chips 同源（`chip hairline bg-card …`），
 * 保持中栏同一套交互语言。
 */
export function QuickReplies({
  options,
  onPick,
  onCustom,
}: {
  options: string[];
  onPick: (text: string) => void;
  onCustom: () => void;
}) {
  if (!options.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="quick-replies">
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          className="chip hairline bg-card text-inksoft hover:bg-cardsunk hover:text-ink transition"
          onClick={() => onPick(opt)}
        >
          {opt}
        </button>
      ))}
      <button
        type="button"
        className="chip hairline bg-card text-inksoft hover:bg-cardsunk hover:text-ink transition"
        onClick={onCustom}
      >
        自定义
      </button>
    </div>
  );
}
