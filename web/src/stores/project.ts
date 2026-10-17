import { create } from "zustand";
import type { SseEvent } from "../types/events";
import type { ProjectSnapshot } from "../types/models";

interface ProjectState {
  projectId: string | null;
  phase: string;
  snapshot: ProjectSnapshot | null;
  /** R06b：回合末快照回读失败的用户可见提示；null = 无异常 */
  refreshError: string | null;
  applyEvent: (ev: SseEvent) => void;
  loadSnapshot: (snap: ProjectSnapshot) => void;   // REST 初始化
  setRefreshError: (msg: string | null) => void;   // R06a/b：回读结果落提示态
  reset: () => void;
}
export const useProjectStore = create<ProjectState>()((set) => ({
  projectId: null, phase: "research_positioning", snapshot: null, refreshError: null,
  applyEvent: (ev) => set(() => {
    if (ev.event === "phase_transition") return { phase: ev.to };
    return {};
  }),
  loadSnapshot: (snap) => set({ projectId: snap.project_id, phase: snap.phase, snapshot: snap }),
  setRefreshError: (msg) => set({ refreshError: msg }),
  reset: () => set({ projectId: null, phase: "research_positioning", snapshot: null, refreshError: null }),
}));
