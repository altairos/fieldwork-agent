import { create } from "zustand";
import type { SseEvent } from "../types/events";
import { stripOptionTags } from "../lib/optionTags";

export interface ChatMessage { role: "user" | "assistant"; content: string; quickReplies?: string[]; }
/** GET /messages 返回项（snake_case 原样直传 hydrate，内部映射为 ChatMessage）。 */
export interface HydrateMessage { role: "user" | "assistant"; content: string; quick_replies?: string[]; }
interface ConversationState {
  messages: ChatMessage[];
  streamBuffer: string;                  // 当前助手消息流式缓冲
  turnRunning: boolean;
  /** 对话史是否已回填完毕（GET /messages → hydrate 落地）。
   *  进入工作台先 reset 置 false，拉取完成 hydrate 置 true；
   *  Timeline 据此区分「加载中」与「真·空会话」，避免加载窗口误显“尚无对话”。 */
  hydrated: boolean;
  lastUsage: Record<string, unknown>;
  quickReplies: string[];                // 待作答的快捷选项（末条 assistant 携带；用户作答后清空）
  applyEvent: (ev: SseEvent) => void;
  pushUser: (text: string) => void;      // useTurn 发送时调用
  hydrate: (msgs: HydrateMessage[]) => void; // 刷新回填：拉 GET /messages 后覆盖式灌入
  beginTurn: () => void;                 // 回合开始：turnRunning=true, 清 buffer
  failTurn: () => void;                  // 回合创建被拒：回滚乐观 UI（见实现处注释）
  cancelTurn: () => void;                // 用户取消：复位流式态，不动 messages（见实现处注释）
  dropLastAssistant: () => void;         // 重新生成：后端已弹出，本地同步丢弃
  reset: () => void;
}
export const useConversationStore = create<ConversationState>()((set) => ({
  messages: [], streamBuffer: "", turnRunning: false, hydrated: false, lastUsage: {}, quickReplies: [],
  applyEvent: (ev) => set((st) => {
    if (ev.event === "message_delta") {
      return { streamBuffer: st.streamBuffer + ev.text, turnRunning: true };
    }
    if (ev.event === "turn_done") {
      // 正文先行剥离 [[options]] 标记（流式 buffer 已含标记文本）；标记语义由
      // quickReplies 状态单独承载，正文与后端持久化口径一致（loop.py 轮末已剥离）
      const content = stripOptionTags(st.streamBuffer);
      const quickReplies = ev.quick_replies ?? [];
      let msgs = st.messages;
      if (content) {
        // hydrate 完整史与 SSE 回放并发的双写保护（轮 4 adopt）：末条已是同文
        // assistant 则不重复 append，否则首页首问会出两条相同回复
        const last = msgs[msgs.length - 1];
        if (!last || last.role !== "assistant" || last.content !== content) {
          // 无选项时不加键：与 hydrate 映射同一 ChatMessage 形状（无值不携带可选字段）
          msgs = [...msgs, { role: "assistant" as const, content, ...(quickReplies.length ? { quickReplies } : {}) }];
        }
      }
      return { messages: msgs, streamBuffer: "", turnRunning: false, lastUsage: ev.usage, quickReplies };
    }
    return {};
  }),
  pushUser: (text) => set((st) => ({
    messages: [...st.messages, { role: "user" as const, content: text }],
    quickReplies: [],                    // 用户已作答（点选或自由输入），按钮退场
  })),
  // 刷新后恢复对话史（App.tsx 拉取后调用）：覆盖式 set messages，不动 turnRunning/streamBuffer。
  // 竞态取舍：仅在与用户手速比拼的毫秒窗口内可能覆盖刚 push 的消息（再刷新自愈），
  // 换取空会话时历史一定能回填——后者是刷新丢史的主要矛盾。
  // quick_replies（snake_case）在此层映射为 quickReplies；末条 assistant 携带选项时
  // 恢复待作答状态（刷新后按钮重现），否则清空。
  hydrate: (msgs) => set(() => {
    const messages: ChatMessage[] = msgs.map((m) => ({
      role: m.role, content: m.content,
      ...(m.quick_replies?.length ? { quickReplies: m.quick_replies } : {}),
    }));
    const last = messages[messages.length - 1];
    return { messages, hydrated: true, quickReplies: last?.role === "assistant" ? (last.quickReplies ?? []) : [] };
  }),
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
  // 重新生成前调用：后端已把末条 assistant 弹出并重放 user 消息，本地只丢这一条；
  // quickReplies 一并清空（旧选项属于已作废的回复）。
  dropLastAssistant: () => set((st) => {
    const msgs = st.messages.slice();
    if (msgs.length && msgs[msgs.length - 1].role === "assistant") msgs.pop();
    return { messages: msgs, quickReplies: [] };
  }),
  reset: () => set({ messages: [], streamBuffer: "", turnRunning: false, hydrated: false, lastUsage: {}, quickReplies: [] }),
}));
