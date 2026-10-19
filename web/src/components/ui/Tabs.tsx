/**
 * Tabs — 右栏 Tab 切换（rp-tab 样式）。
 * 设计板同源：
 *   基座（line 543-547）：`text-[11.5px] px-2.5 py-1 rounded-md hairline bg-card text-inksoft`
 *   选中（line 75 `.rp-tab[aria-selected="true"]`）：`background:#F6EBE6; color:#A8412E; border-color:#EBD5CD`
 *   → token 化：bg-cinnabarwash text-cinnabar；#EBD5CD 已主题化为 tintline。
 * 等宽：grid-cols-3 固定三列（仅 RightDock 使用，5 tab 两行），选中态勾选图标不引起
 *   宽度跳动；内容居中 + whitespace-nowrap，窄右栏下仍完整可读。
 * cols=2 供中栏「对话/检索结果」双 tab（spec C1）；右栏缺省 3 列不变。
 * 选中态 = 实边框 + 色变 + IconCheck 勾选标记（交互规范：选中需明显标识）。
 *
 * 无障碍（评审 R6，对齐 WAI-ARIA tabs pattern）：
 *   - role=tablist/tab + aria-selected（原有）；
 *   - **aria-controls**：tab 指向对应 tabpanel（id 由 `tabIds(idBase)` 生成，消费方用同一个
 *     helper 给面板挂 id/aria-labelledby，两侧不可能拼错）；
 *   - **方向键**：←/→ 循环切换、Home/End 到首尾，且焦点跟随选中（roving tabindex：
 *     选中项 tabIndex=0，其余 -1，Tab 键只在 tablist 上停一次）；
 *   - 红点为纯装饰（aria-hidden）。
 * 面板是懒挂载的（未选中不渲染），因此非活动 tab 的 aria-controls 在那一刻指向不存在的
 * 节点——这是 tabs 懒加载的通行取舍；消费方务必给**活动**面板挂上对应 id。
 */
import { useId, useRef } from "react";
import { IconCheck } from "./icons";

// Tailwind JIT 不识别动态拼接类名 → 静态映射（spec C1）
const GRID: Record<2 | 3, string> = { 2: "grid-cols-2", 3: "grid-cols-3" };

/** tab / tabpanel 的 id 契约（唯一来源，避免两侧各拼各的）。 */
export function tabIds(base: string) {
  return {
    tab: (id: string) => `${base}-tab-${id}`,
    panel: (id: string) => `${base}-panel-${id}`,
  };
}

export interface TabItem {
  id: string;
  label: string;
  /** 红点：该 tab 有新内容待看（如 evidence 新证据）；纯装饰，aria-hidden */
  dot?: boolean;
}

export function Tabs({
  tabs,
  active,
  onChange,
  cols,
  className = "",
  idBase,
}: {
  tabs: TabItem[];
  active: string;
  onChange: (id: string) => void;
  cols?: 2 | 3;
  className?: string;
  /** id 前缀；面板要用 `tabIds(idBase)` 生成同一个 id。缺省用 useId（仅在本组件内有意义） */
  idBase?: string;
}) {
  const auto = useId();
  const ids = tabIds(idBase ?? auto);
  const listRef = useRef<HTMLDivElement>(null);

  // 键盘切换（WAI-ARIA：自动激活型）。焦点在 tablist 作用域内按 id 找：
  // useId 生成的 id 含 ":"，做 CSS 选择器要转义，用属性选择器更稳，也比 getElementById 少一层全局查找。
  const focusTab = (id: string) => {
    listRef.current?.querySelector<HTMLButtonElement>(`[id="${id}"]`)?.focus();
  };
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const idx = tabs.findIndex((t) => t.id === active);
    if (idx < 0) return;
    let next: number;
    if (e.key === "ArrowRight") next = (idx + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    else return;
    e.preventDefault();
    const target = tabs[next];
    focusTab(ids.tab(target.id));
    onChange(target.id);
  };

  return (
    <div
      ref={listRef}
      className={`grid ${GRID[cols ?? 3]} gap-1 ${className}`.trim()}
      role="tablist"
      onKeyDown={onKeyDown}
    >
      {tabs.map((t) => {
        const selected = t.id === active;
        return (
          <button
            key={t.id}
            id={ids.tab(t.id)}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={ids.panel(t.id)}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(t.id)}
            className={`tapzone text-[11.5px] px-2 py-1 rounded-md border transition flex items-center justify-center gap-1 whitespace-nowrap ${
              selected
                ? "bg-cinnabarwash text-cinnabar border-tintline"
                : "bg-card text-inksoft border-hairline hover:text-ink hover:bg-cardsunk"
            }`}
          >
            {selected && <IconCheck className="w-3 h-3 shrink-0" aria-hidden="true" />}
            {t.label}
            {t.dot && <span className="w-1.5 h-1.5 rounded-full bg-cinnabar shrink-0" aria-hidden="true" />}
          </button>
        );
      })}
    </div>
  );
}
