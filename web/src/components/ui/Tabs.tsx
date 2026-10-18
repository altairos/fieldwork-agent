/**
 * Tabs — 右栏 Tab 切换（rp-tab 样式）。
 * 设计板同源：
 *   基座（line 543-547）：`text-[11.5px] px-2.5 py-1 rounded-md hairline bg-card text-inksoft`
 *   选中（line 75 `.rp-tab[aria-selected="true"]`）：`background:#F6EBE6; color:#A8412E; border-color:#EBD5CD`
 *   → token 化：bg-cinnabarwash text-cinnabar；#EBD5CD 已主题化为 tintline。
 * 等宽：grid-cols-3 固定三列（仅 RightDock 使用，5 tab 两行），选中态勾选图标不引起
 *   宽度跳动；内容居中 + whitespace-nowrap，窄右栏下仍完整可读。
 * 选中态 = 实边框 + 色变 + IconCheck 勾选标记（交互规范：选中需明显标识）。
 * 无障碍：role=tablist/tab + aria-selected（line 542-547）。
 */
import { IconCheck } from "./icons";
export interface TabItem {
  id: string;
  label: string;
}

export function Tabs({
  tabs,
  active,
  onChange,
  className = "",
}: {
  tabs: TabItem[];
  active: string;
  onChange: (id: string) => void;
  className?: string;
}) {
  return (
    <div className={`grid grid-cols-3 gap-1 ${className}`.trim()} role="tablist">
      {tabs.map((t) => {
        const selected = t.id === active;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(t.id)}
            className={`tapzone text-[11.5px] px-2 py-1 rounded-md border transition flex items-center justify-center gap-1 whitespace-nowrap ${
              selected
                ? "bg-cinnabarwash text-cinnabar border-tintline"
                : "bg-card text-inksoft border-hairline hover:text-ink hover:bg-cardsunk"
            }`}
          >
            {selected && <IconCheck className="w-3 h-3 shrink-0" aria-hidden="true" />}
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
