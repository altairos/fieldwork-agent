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
  hydrate: (msgs: ChatMessage[]) => void; // 刷新回填：拉 GET /messages 后覆盖式灌入
  beginTurn: () => void;                 // 回合开始：turnRunning=true, 清 buffer
  failTurn: () => void;                  // 回合创建被拒：回滚乐观 UI（见实现处注释）
  cancelTurn: () => void;                // 用户取消：复位流式态，不动 messages（见实现处注释）
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
      let msgs = st.messages;
      if (content) {
        // hydrate 完整史与 SSE 回放并发的双写保护（轮 4 adopt）：末条已是同文
        // assistant 则不重复 append，否则首页首问会出两条相同回复
        const last = msgs[msgs.length - 1];
        if (!last || last.role !== "assistant" || last.content !== content) {
          msgs = [...msgs, { role: "assistant" as const, content }];
        }
      }
      return { messages: msgs, streamBuffer: "", turnRunning: false, lastUsage: ev.usage };
    }
    return {};
  }),
  pushUser: (text) => set((st) => ({ messages: [...st.messages, { role: "user", content: text }] })),
  // 刷新后恢复对话史（App.tsx 拉取后调用）：覆盖式 set messages，不动 turnRunning/streamBuffer。
  // 竞态取舍：仅在与用户手速比拼的毫秒窗口内可能覆盖刚 push 的消息（再刷新自愈），
  // 换取空会话时历史一定能回填——后者是刷新丢史的主要矛盾。
  hydrate: (msgs) => set({ messages: msgs }),
  beginTurn: () => set({ turnRunning: true, streamBuffer: "" }),
  // POST /messages 失败（含 409）时由 useTurn 调用：末条若是刚 push 的 user 消息则弹出，
  // 并复位 turnRunning/streamBuffer，避免打字光标永久脉冲 + 对话流留下后端未接受的消息。
  // 只用于"回合未被接受"的场景；SSE 阶段的消息已被后端接受，绝不弹出。
  failTurn: () => set((st) => {
    const msgs = st.messages.slice();
    if (msgs.length && msgs[msgs.length - 1].role === "user") msgs.pop();
    return { messages: msgs, turnRunning: false, streamBuffer: "" };
  }),
  // 用户取消（P2-9）：SSE 断开收不到终态事件，turnRunning/streamBuffer 须主动复位，
  // 否则打字光标永久脉冲；残缺 streamBuffer 丢弃（对齐后端：取消的残缺回复不入史），
  // messages 不动——user 消息已被后端接受，与 failTurn（回合被拒弹消息）语义相反。
  cancelTurn: () => set({ turnRunning: false, streamBuffer: "" }),
  reset: () => set({ messages: [], streamBuffer: "", turnRunning: false, lastUsage: {} }),
}));
