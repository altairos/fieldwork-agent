/**
 * Tabs — 右栏 Tab 切换（rp-tab 样式）。
 * 设计板同源：
 *   基座（line 543-547）：`text-[11.5px] px-2.5 py-1 rounded-md hairline bg-card text-inksoft`
 *   选中（line 75 `.rp-tab[aria-selected="true"]`）：`background:#F6EBE6; color:#A8412E; border-color:#EBD5CD`
 *   → token 化：bg-cinnabarwash text-cinnabar；#EBD5CD 为设计板 token 外用值，按约束保留。
 * 无障碍：role=tablist/tab + aria-selected（line 542-547）。
 */
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
            className={`text-[11.5px] px-2.5 py-1 rounded-md border transition ${
              selected
                ? "bg-cinnabarwash text-cinnabar border-[#EBD5CD]"
                : "bg-card text-inksoft border-hairline hover:text-ink hover:bg-cardsunk"
            }`}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
