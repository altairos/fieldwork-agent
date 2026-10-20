import { create } from "zustand";
import { visibilityOf } from "../lib/toolMeta";
import { useToolsStore } from "./tools";
import type { SseEvent } from "../types/events";
import type { ErrorEvent } from "../types/events";

export type RightTab = "basic-info" | "sites" | "informants" | "budget" | "knowledge";
/** 中栏 tab（spec C1）：chat=对话时间线，evidence=检索结果（EvidencePanel） */
export type MiddleTab = "chat" | "evidence";
interface UiState {
  activeTab: RightTab;
  degradedBanner: ErrorEvent | null;
  /** 中栏「对话/检索结果」当前 tab（spec C1）；新 collapsed 证据到达只置红点不自动切（红线） */
  middleTab: MiddleTab;
  evidenceHint: boolean;                     // 红点：evidence tab 有未看的新可展示证据
  /** 移动端（<lg）形态：左栏抽屉收起与否；桌面（lg+）左栏常驻网格列，不消费此状态 */
  leftCollapsed: boolean;
  /** 移动端（<lg）形态：右栏底部面板折叠与否；桌面（lg+）右栏常驻网格列，不消费此状态 */
  rightCollapsed: boolean;
  /** 示例 chip → 输入框的草稿通道（消费一次即清）：Timeline 空态写入，Composer 读取后回 null */
  composerDraft: string | null;
  applyEvent: (ev: SseEvent) => void;
  setActiveTab: (t: RightTab) => void;
  setMiddleTab: (t: MiddleTab) => void;
  setComposerDraft: (t: string | null) => void;
  dismissBanner: () => void;
  toggleLeft: () => void;
  toggleRight: () => void;
  reset: () => void;
}
// 阶段 → 建议/查看 tab 映射（导出供 App 的 LeftRail onSelect 复用；A/D 阶段对应基本信息）
export const PHASE_TAB: Record<string, RightTab> = {
  research_positioning: "basic-info",
  field_site_profiles: "sites", itinerary_planning: "budget", proposal_generation: "basic-info",
};
export const useUiStore = create<UiState>()((set, get) => ({
  activeTab: "basic-info", degradedBanner: null,
  middleTab: "chat", evidenceHint: false,
  leftCollapsed: true, rightCollapsed: true,
  composerDraft: null,
  applyEvent: (ev) => set(() => {
    if (ev.event === "phase_transition") {
      const tab = PHASE_TAB[ev.to];
      // 阶段切换 → 右栏**直接**切到该阶段对应面板（用户反馈：原来是 toast 建议用户手点
      // 「查看」，多一步且容易被忽略）。无映射的阶段（未知 id）保留当前 tab 不动。
      // 注意：仅 SSE phase_transition 触发；进入工作台时按快照回填 phase 不动 tab。
      return tab ? { activeTab: tab } : {};
    }
    if (ev.event === "error") {
      return { degradedBanner: { code: ev.code, message: ev.message, hint: ev.hint } };
    }
    if (ev.event === "tool_completed" && ev.ok) {
      // 新可展示证据到达 → 红点（绝不自动切 tab，红线）；工具名查 tools store
      // （dispatchEvent 顺序：tools 先于 ui，byCallId 必已就位）
      const tool = useToolsStore.getState().byCallId[ev.call_id]?.tool ?? "";
      if (visibilityOf(tool) === "collapsed" && get().middleTab !== "evidence") {
        return { evidenceHint: true };
      }
    }
    return {};
  }),
  setActiveTab: (t) => set({ activeTab: t }),
  setMiddleTab: (t) => set((st) => ({ middleTab: t, evidenceHint: t === "evidence" ? false : st.evidenceHint })),
  setComposerDraft: (t) => set({ composerDraft: t }),
  dismissBanner: () => set({ degradedBanner: null }),
  toggleLeft: () => set((st) => ({ leftCollapsed: !st.leftCollapsed })),
  toggleRight: () => set((st) => ({ rightCollapsed: !st.rightCollapsed })),
  reset: () => set({ activeTab: "basic-info", degradedBanner: null, middleTab: "chat", evidenceHint: false, leftCollapsed: true, rightCollapsed: true, composerDraft: null }),
}));
