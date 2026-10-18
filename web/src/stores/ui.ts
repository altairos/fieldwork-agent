import { create } from "zustand";
import type { SseEvent } from "../types/events";
import type { ErrorEvent } from "../types/events";

export type RightTab = "basic-info" | "sites" | "informants" | "budget" | "knowledge";
interface UiState {
  activeTab: RightTab;
  pendingSuggestion: { tab: RightTab; reason: string } | null;   // 切换建议(不自动切)
  degradedBanner: ErrorEvent | null;
  /** 移动端（<lg）形态：左栏抽屉收起与否；桌面（lg+）左栏常驻网格列，不消费此状态 */
  leftCollapsed: boolean;
  /** 移动端（<lg）形态：右栏底部面板折叠与否；桌面（lg+）右栏常驻网格列，不消费此状态 */
  rightCollapsed: boolean;
  /** 示例 chip → 输入框的草稿通道（消费一次即清）：Timeline 空态写入，Composer 读取后回 null */
  composerDraft: string | null;
  applyEvent: (ev: SseEvent) => void;
  setActiveTab: (t: RightTab) => void;
  setComposerDraft: (t: string | null) => void;
  acceptSuggestion: () => void;      // 用户点"去查看"→ 切 tab 并清建议
  dismissSuggestion: () => void;
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
  activeTab: "basic-info", pendingSuggestion: null, degradedBanner: null,
  leftCollapsed: true, rightCollapsed: true,
  composerDraft: null,
  applyEvent: (ev) => set(() => {
    if (ev.event === "phase_transition") {
      const tab = PHASE_TAB[ev.to];
      // 关键不变量：不自动改 activeTab，只置建议
      return tab ? { pendingSuggestion: { tab, reason: `已进入${ev.to}阶段` } } : {};
    }
    if (ev.event === "error") {
      return { degradedBanner: { code: ev.code, message: ev.message, hint: ev.hint } };
    }
    return {};
  }),
  setActiveTab: (t) => set({ activeTab: t }),
  setComposerDraft: (t) => set({ composerDraft: t }),
  acceptSuggestion: () => { const s = get().pendingSuggestion; if (s) set({ activeTab: s.tab, pendingSuggestion: null }); },
  dismissSuggestion: () => set({ pendingSuggestion: null }),
  dismissBanner: () => set({ degradedBanner: null }),
  toggleLeft: () => set((st) => ({ leftCollapsed: !st.leftCollapsed })),
  toggleRight: () => set((st) => ({ rightCollapsed: !st.rightCollapsed })),
  reset: () => set({ activeTab: "basic-info", pendingSuggestion: null, degradedBanner: null, leftCollapsed: true, rightCollapsed: true, composerDraft: null }),
}));
