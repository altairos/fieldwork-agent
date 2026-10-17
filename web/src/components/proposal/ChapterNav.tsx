/**
 * ChapterNav — 提案八章垂直导航（滚动高亮当前章）。
 * 设计板同源：view-proposal 左栏 nav（lines 696-705）：
 *   容器 `flex-1 overflow-y-auto scroll px-3 py-3 space-y-0.5 text-[13px]`（line 696）
 *   普通项 `block px-2.5 py-1.5 rounded-md text-inksoft hover:bg-card/70`（line 697）
 *   当前项 `bg-card border border-cinnabar/25 text-cinnabar font-medium`（line 699）
 * activeIndex / 滚动监听由 ProposalView 持有（IntersectionObserver），本组件纯受控。
 */
export function ChapterNav({
  sections,
  activeIndex,
  onSelect,
}: {
  /** 章节标题列表（后端 sections：markdown 中 `## ` 行） */
  sections: string[];
  activeIndex: number;
  onSelect: (index: number) => void;
}) {
  return (
    <nav className="flex-1 overflow-y-auto scroll overscroll-contain px-3 py-3 space-y-0.5 text-[13px] min-h-0" aria-label="提案章节导航">
      {sections.map((title, i) => {
        const active = i === activeIndex;
        return (
          <button
            key={`${i}-${title}`}
            type="button"
            onClick={() => onSelect(i)}
            aria-current={active ? "true" : undefined}
            className={`block w-full text-left px-2.5 py-1.5 rounded-md transition ${
              active
                ? "bg-card border border-cinnabar/25 text-cinnabar font-medium"
                : "text-inksoft hover:bg-card/70"
            }`}
          >
            {title}
          </button>
        );
      })}
    </nav>
  );
}
