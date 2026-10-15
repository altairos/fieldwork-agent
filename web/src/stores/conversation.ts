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
  failTurn: () => void;                  // 回合创建被拒：回滚乐观 UI（见实现处注释）
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
  // POST /messages 失败（含 409）时由 useTurn 调用：末条若是刚 push 的 user 消息则弹出，
  // 并复位 turnRunning/streamBuffer，避免打字光标永久脉冲 + 对话流留下后端未接受的消息。
  // 只用于"回合未被接受"的场景；SSE 阶段的消息已被后端接受，绝不弹出。
  failTurn: () => set((st) => {
    const msgs = st.messages.slice();
    if (msgs.length && msgs[msgs.length - 1].role === "user") msgs.pop();
    return { messages: msgs, turnRunning: false, streamBuffer: "" };
  }),
  reset: () => set({ messages: [], streamBuffer: "", turnRunning: false, lastUsage: {} }),
}));
