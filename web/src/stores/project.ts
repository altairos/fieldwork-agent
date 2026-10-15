import { create } from "zustand";
import type { SseEvent } from "../types/events";
import type { ProjectSnapshot } from "../types/models";

interface ProjectState {
  projectId: string | null;
  phase: string;
  snapshot: ProjectSnapshot | null;
  applyEvent: (ev: SseEvent) => void;
  loadSnapshot: (snap: ProjectSnapshot) => void;   // REST 初始化
  reset: () => void;
}
export const useProjectStore = create<ProjectState>()((set) => ({
  projectId: null, phase: "research_positioning", snapshot: null,
  applyEvent: (ev) => set(() => {
    if (ev.event === "phase_transition") return { phase: ev.to };
    return {};
  }),
  loadSnapshot: (snap) => set({ projectId: snap.project_id, phase: snap.phase, snapshot: snap }),
  reset: () => set({ projectId: null, phase: "research_positioning", snapshot: null }),
}));
