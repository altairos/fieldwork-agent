import type { ComponentType, ReactNode } from "react";
import { useUiStore, type RightTab } from "../../stores/ui";
import { BasicInfoPanel } from "../right/BasicInfoPanel";
import { BudgetPanel } from "../right/BudgetPanel";
import { FieldSitesPanel } from "../right/FieldSitesPanel";
import { InformantsPanel } from "../right/InformantsPanel";
import { KnowledgePanel } from "../right/KnowledgePanel";
import { Tabs, type TabItem } from "../ui/Tabs";
import { IconSpark } from "../ui/icons";

/**
 * RightDock — 右栏上下文面板容器（状态投影）。
 * 设计板同源：workspace 右栏 lines 529-674：
 *   容器 `bg-paperdeep border-l border-hairline flex flex-col min-w-0 relative`（line 530）
 *   切换建议 toast（lines 532-538）：`absolute top-2 left-3 right-3 z-10 rounded-lg bg-card shadow-pop hairline p-3`
 *     + spark 朱砂图标 + 「查看/稍后」— 设计红线①：不自动强切 Tab，用户点「查看」(acceptSuggestion) 才切
 *     （ui store 的不变量：phase_transition 只置 pendingSuggestion，绝不改 activeTab）
 *   面板头（lines 540-548）：「上下文面板 · 状态投影」tracking 标签 + rp-tab 组（基本信息/田野点/访谈对象/预算/知识库）
 *   内容区（line 551）：`flex-1 overflow-y-auto scroll p-4`，按 activeTab 渲染五面板（Task 7 接线）。
 *
 * Tab id 对齐（brief 红线）：TABS 的 id 与 PANELS 的键**一律取 ui store 的 RightTab 联合类型**
 * （basic-info/sites/informants/budget/knowledge）；`Record<RightTab, …>` 是穷尽映射，
 * 若任一侧 id 漂移（如旧 site/informant/kb）TS 直接报错，杜绝接线错位。
 * 默认消费 useUiStore 的 activeTab/pendingSuggestion；也可用 props 受控覆盖（children 为插槽逃生口）。
 */
const TABS: TabItem[] = [
  { id: "basic-info", label: "基本信息" },
  { id: "sites", label: "田野点" },
  { id: "informants", label: "访谈对象" },
  { id: "budget", label: "预算" },
  { id: "knowledge", label: "知识库" },
];

/** activeTab → 面板组件（穷尽 RightTab，五面板同源消费 snapshot + tools store）。 */
const PANELS: Record<RightTab, ComponentType> = {
  "basic-info": BasicInfoPanel,
  sites: FieldSitesPanel,
  informants: InformantsPanel,
  budget: BudgetPanel,
  knowledge: KnowledgePanel,
};

export function RightDock({
  activeTab,
  onTabChange,
  children,
}: {
  activeTab?: RightTab;
  onTabChange?: (tab: RightTab) => void;
  children?: ReactNode;
}) {
  const storeTab = useUiStore((s) => s.activeTab);
  const setActiveTab = useUiStore((s) => s.setActiveTab);
  const suggestion = useUiStore((s) => s.pendingSuggestion);
  const acceptSuggestion = useUiStore((s) => s.acceptSuggestion);
  const dismissSuggestion = useUiStore((s) => s.dismissSuggestion);

  const tab = activeTab ?? storeTab;
  const handleChange = (id: string) => {
    const next = id as RightTab;
    if (onTabChange) onTabChange(next);
    else setActiveTab(next);
  };
  const suggestionLabel = suggestion ? TABS.find((t) => t.id === suggestion.tab)?.label ?? suggestion.tab : null;
  const Panel = PANELS[tab] ?? BasicInfoPanel;

  return (
    <div className="bg-paperdeep border-l border-hairline flex flex-col min-w-0 min-h-0 relative">
      {/* 切换建议 toast（不自动切，用户点「查看」才切） */}
      {suggestion && (
        <div className="absolute top-2 left-3 right-3 z-10 rounded-lg bg-card shadow-pop hairline p-3 flex items-start gap-2.5">
          <IconSpark className="w-4 h-4 text-cinnabar mt-0.5 shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="text-[12px] text-ink leading-snug">
              {suggestion.reason}，建议切换到「{suggestionLabel}」面板查看
            </div>
            <div className="flex gap-2 mt-1.5">
              {/* tapzone：触屏热区外扩至 ≥24px（批次 B；纯文字钮视觉保持不变） */}
              <button type="button" onClick={acceptSuggestion} className="tapzone text-[11.5px] text-cinnabar font-medium">
                查看
              </button>
              <button type="button" onClick={dismissSuggestion} className="tapzone text-[11.5px] text-inkfaint">
                稍后
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="px-4 pt-3 pb-2 border-b border-hairline">
        <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">上下文面板 · 状态投影</div>
        <Tabs tabs={TABS} active={tab} onChange={handleChange} />
      </div>

      {/* overscroll-contain（批次 C）：移动端底部面板内滚动不穿透到底层对话流 */}
      <div className="flex-1 overflow-y-auto scroll overscroll-contain p-4">{children ?? <Panel />}</div>
    </div>
  );
}
