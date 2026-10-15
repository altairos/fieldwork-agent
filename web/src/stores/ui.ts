import { create } from "zustand";
import type { SseEvent } from "../types/events";
import type { ErrorEvent } from "../types/events";

export type RightTab = "progress" | "sites" | "informants" | "budget" | "knowledge";
interface UiState {
  activeTab: RightTab;
  pendingSuggestion: { tab: RightTab; reason: string } | null;   // 切换建议(不自动切)
  degradedBanner: ErrorEvent | null;
  leftCollapsed: boolean;
  applyEvent: (ev: SseEvent) => void;
  setActiveTab: (t: RightTab) => void;
  acceptSuggestion: () => void;      // 用户点"去查看"→ 切 tab 并清建议
  dismissSuggestion: () => void;
  dismissBanner: () => void;
  toggleLeft: () => void;
  reset: () => void;
}
// 阶段 → 建议 tab 映射
const PHASE_TAB: Record<string, RightTab> = {
  field_site_profiles: "sites", itinerary_planning: "budget", proposal_generation: "progress",
};
export const useUiStore = create<UiState>()((set, get) => ({
  activeTab: "progress", pendingSuggestion: null, degradedBanner: null, leftCollapsed: false,
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
  acceptSuggestion: () => { const s = get().pendingSuggestion; if (s) set({ activeTab: s.tab, pendingSuggestion: null }); },
  dismissSuggestion: () => set({ pendingSuggestion: null }),
  dismissBanner: () => set({ degradedBanner: null }),
  toggleLeft: () => set((st) => ({ leftCollapsed: !st.leftCollapsed })),
  reset: () => set({ activeTab: "progress", pendingSuggestion: null, degradedBanner: null, leftCollapsed: false }),
}));
