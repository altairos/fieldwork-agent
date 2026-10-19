import { create } from "zustand";
import type { SseEvent, ToolErrorInfo } from "../types/events";

export interface ToolCall {
  call_id: string; tool: string; args: Record<string, unknown>; purpose: string;
  /** R15 回合归属：采集自 tool_started 封套平铺的 turn_id/seq（旧事件缺省 undefined） */
  turnId?: string; seq?: number;
  status: "running" | "done" | "failed"; ok?: boolean; data?: unknown; degraded?: string | null;
  error?: ToolErrorInfo | null;
}
interface ToolsState {
  byCallId: Record<string, ToolCall>;
  order: string[];                       // call_id 出现顺序（渲染稳定排序）
  applyEvent: (ev: SseEvent) => void;
  reset: () => void;
}
export const useToolsStore = create<ToolsState>()((set) => ({
  byCallId: {}, order: [],
  applyEvent: (ev) => set((st) => {
    if (ev.event === "tool_started") {
      if (st.byCallId[ev.call_id]) return {};          // 幂等：已存在不重置
      return {
        byCallId: { ...st.byCallId, [ev.call_id]: {
          call_id: ev.call_id, tool: ev.tool, args: ev.args, purpose: ev.purpose, status: "running",
          turnId: ev.turn_id, seq: ev.seq } },
        order: [...st.order, ev.call_id],
      };
    }
    if (ev.event === "tool_completed") {
      const prev = st.byCallId[ev.call_id];
      if (!prev) return {};                            // 无 started 的孤立 completed：忽略
      return { byCallId: { ...st.byCallId, [ev.call_id]: {
        ...prev, status: ev.ok ? "done" : "failed", ok: ev.ok, data: ev.data, degraded: ev.degraded ?? null,
        error: ev.error ?? null } } };
    }
    return {};
  }),
  reset: () => set({ byCallId: {}, order: [] }),
}));
