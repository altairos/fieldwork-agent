import { create } from "zustand";
import type { SseEvent } from "../types/events";

export interface ChatMessage { role: "user" | "assistant"; content: string; }
interface ConversationState {
  messages: ChatMessage[];
  streamBuffer: string;                  // 当前助手消息流式缓冲
  turnRunning: boolean;
  lastUsage: Record<string, unknown>;
  applyEvent: (ev: SseEvent) => void;
  pushUser: (text: string) => void;      // useTurn 发送时调用
  beginTurn: () => void;                 // 回合开始：turnRunning=true, 清 buffer
  reset: () => void;
}
export const useConversationStore = create<ConversationState>()((set) => ({
  messages: [], streamBuffer: "", turnRunning: false, lastUsage: {},
  applyEvent: (ev) => set((st) => {
    if (ev.event === "message_delta") {
      return { streamBuffer: st.streamBuffer + ev.text, turnRunning: true };
    }
    if (ev.event === "turn_done") {
      const content = st.streamBuffer;
      const msgs = content ? [...st.messages, { role: "assistant" as const, content }] : st.messages;
      return { messages: msgs, streamBuffer: "", turnRunning: false, lastUsage: ev.usage };
    }
    return {};
  }),
  pushUser: (text) => set((st) => ({ messages: [...st.messages, { role: "user", content: text }] })),
  beginTurn: () => set({ turnRunning: true, streamBuffer: "" }),
  reset: () => set({ messages: [], streamBuffer: "", turnRunning: false, lastUsage: {} }),
}));
