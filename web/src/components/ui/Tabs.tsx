/**
 * Tabs — 右栏 Tab 切换（rp-tab 样式）。
 * 设计板同源：
 *   基座（line 543-547）：`text-[11.5px] px-2.5 py-1 rounded-md hairline bg-card text-inksoft`
 *   选中（line 75 `.rp-tab[aria-selected="true"]`）：`background:#F6EBE6; color:#A8412E; border-color:#EBD5CD`
 *   → token 化：bg-cinnabarwash text-cinnabar；#EBD5CD 已主题化为 tintline。
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
    <div className={`flex gap-1 flex-wrap ${className}`.trim()} role="tablist">
      {tabs.map((t) => {
        const selected = t.id === active;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(t.id)}
            className={`tapzone text-[11.5px] px-2.5 py-1 rounded-md border transition inline-flex items-center gap-1 ${
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
