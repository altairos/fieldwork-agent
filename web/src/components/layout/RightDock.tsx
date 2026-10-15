import type { ReactNode } from "react";
import { useUiStore, type RightTab } from "../../stores/ui";
import { EmptyState } from "../ui/EmptyState";
import { Tabs, type TabItem } from "../ui/Tabs";
import { IconCompass, IconSpark } from "../ui/icons";

/**
 * RightDock — 右栏上下文面板容器（状态投影）。
 * 设计板同源：workspace 右栏 lines 529-674：
 *   容器 `bg-paperdeep border-l border-hairline flex flex-col min-w-0 relative`（line 530）
 *   切换建议 toast（lines 532-538）：`absolute top-2 left-3 right-3 z-10 rounded-lg bg-card shadow-pop hairline p-3`
 *     + spark 朱砂图标 + 「查看/稍后」— 设计红线①：不自动强切 Tab，用户确认才切（store 行为 Task 3 已保证，此处只做 UI）
 *   面板头（lines 540-548）：「上下文面板 · 状态投影」tracking 标签 + rp-tab 组（进度/田野点/访谈对象/预算/知识库）
 *   内容区（line 551）：`flex-1 overflow-y-auto scroll p-4`（各面板内容 Task 7 填充，这里只留 children 插槽）。
 * 默认消费 useUiStore 的 activeTab/pendingSuggestion；也可用 props 受控覆盖。
 */
const TABS: TabItem[] = [
  { id: "progress", label: "进度" },
  { id: "sites", label: "田野点" },
  { id: "informants", label: "访谈对象" },
  { id: "budget", label: "预算" },
  { id: "knowledge", label: "知识库" },
];

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
              <button type="button" onClick={acceptSuggestion} className="text-[11.5px] text-cinnabar font-medium">
                查看
              </button>
              <button type="button" onClick={dismissSuggestion} className="text-[11.5px] text-inkfaint">
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

      <div className="flex-1 overflow-y-auto scroll p-4">
        {children ?? (
          <EmptyState
            icon={<IconCompass className="w-4 h-4" />}
            title="面板内容建设中"
            hint="工具结果卡将在此投影（Task 7）"
          />
        )}
      </div>
    </div>
  );
}
