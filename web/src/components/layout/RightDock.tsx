import type { ComponentType, ReactNode } from "react";
import { useUiStore, type RightTab } from "../../stores/ui";
import { BasicInfoPanel } from "../right/BasicInfoPanel";
import { BudgetPanel } from "../right/BudgetPanel";
import { FieldSitesPanel } from "../right/FieldSitesPanel";
import { InformantsPanel } from "../right/InformantsPanel";
import { KnowledgePanel } from "../right/KnowledgePanel";
import { Tabs, tabIds, type TabItem } from "../ui/Tabs";
import { IconCompass } from "../ui/icons";

/**
 * RightDock — 右栏上下文面板容器（实时状态）。
 * 设计板同源：workspace 右栏 lines 529-674：
 *   容器 `bg-paperdeep border-l border-hairline flex flex-col min-w-0 relative`（line 530）
 *   切换建议 toast（设计板 lines 532-538）——**已移除**（用户反馈）：阶段推进时右栏
 *     直接切到该阶段对应面板（stores/ui.ts 的 phase_transition 分支），不再弹 toast
 *     让用户手点「查看」；那一步既多余又容易被忽略。
 *   面板头（lines 540-548）：「上下文面板 · 实时状态」tracking 标签 + rp-tab 组
 *   内容区（line 551）：`flex-1 overflow-y-auto scroll p-4`，按 activeTab 渲染五面板。
 *   底部声明栏（2026-10-18）：五个 Tab 常驻一致。
 *
 * Tab id 对齐（brief 红线）：TABS 的 id 与 PANELS 的键**一律取 ui store 的 RightTab 联合类型**
 * （basic-info/sites/informants/budget/knowledge）；`Record<RightTab, …>` 是穷尽映射，
 * 若任一侧 id 漂移（如旧 site/informant/kb）TS 直接报错，杜绝接线错位。
 */
/** tab ↔ tabpanel 的 id 契约（与 Tabs 的 idBase 同源，两侧拼错不可能） */
const IDS = tabIds("rightdock");

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

  const tab = activeTab ?? storeTab;
  const handleChange = (id: string) => {
    const next = id as RightTab;
    if (onTabChange) onTabChange(next);
    else setActiveTab(next);
  };
  const Panel = PANELS[tab] ?? BasicInfoPanel;

  return (
    <div className="fw-glass g1 border-l border-hairline flex flex-col w-full min-w-0 min-h-0 relative">
      <div className="px-4 pt-3 pb-2 border-b border-hairline">
        <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-2">上下文面板 · 实时状态</div>
        <Tabs tabs={TABS} active={tab} onChange={handleChange} idBase="rightdock" />
      </div>

      {/* overscroll-contain（批次 C）：移动端底部面板内滚动不穿透到底层对话流 */}
      <div
        role="tabpanel"
        id={IDS.panel(tab)}
        aria-labelledby={IDS.tab(tab)}
        className="flex-1 overflow-y-auto scroll overscroll-contain p-4"
      >
        {children ?? <Panel />}
      </div>

      {/* 实时口径声明栏：五 Tab 常驻一致（不随面板滚动，对齐 dock 底缘） */}
      <div className="border-t border-hairline px-4 py-2 flex items-center gap-2 text-[11px] leading-snug text-inkfaint">
        <IconCompass className="w-3.5 h-3.5 shrink-0" />
        本面板实时同步系统状态：数值与文本一律来自内核快照与工具返回，不经模型转述。
      </div>
    </div>
  );
}
