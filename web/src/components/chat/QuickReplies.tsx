/**
 * QuickReplies — 快捷作答 chips（B 节问题 4）。
 *
 * 数据源：conversation store `quickReplies`（turn_done 事件 / hydrate 恢复），
 * 由 Composer 在输入壳上方渲染；用户点选即发送（onPick 直调 api.send），
 * 「自定义」聚焦输入框转入打字。
 * B1 样式升级：选项按钮走朱砂主色（浅洗底 + 主色描边，hover 实心，
 * 与 Button primary 同语言），padding 覆盖 chip 基类增强按钮感；
 * 「自定义」保持 hairline 次级样式区分主次。
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
          className="chip px-3 py-1.5 bg-cinnabarwash text-cinnabar border-cinnabar/50 hover:bg-cinnabar hover:text-ontint hover:border-cinnabar cursor-pointer transition"
          onClick={() => onPick(opt)}
        >
          {opt}
        </button>
      ))}
      <button
        type="button"
        className="chip hairline px-3 py-1.5 bg-card text-inksoft hover:bg-cardsunk hover:text-ink cursor-pointer transition"
        onClick={onCustom}
      >
        自定义
      </button>
    </div>
  );
}
