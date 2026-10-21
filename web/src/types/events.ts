/**
 * SSE 事件类型契约 — 对齐 Plan 3 `web_host/events.py`（六事件 + EventEnvelope）。
 *
 * 线上格式（web_host/sse.py encode_event）：
 *   SSE `data:` 行 = {"turn_id", "seq", ...事件字段平铺}
 *   `event:` 行 = 事件名；`id:` 行 = seq
 */

// ---------------------------------------------------------------- 六事件 payload
// （data 内除 turn_id/seq 外的字段）

/** 流式回复文本增量。 */
export interface MessageDelta {
  text: string;
}

/** P2-7：助手正文即将开始的信号（每回合一次，先于首个 message_delta）。
 *  录制/自动化据此判断“首条回复已开始”，不再靠页面文本猜测。
 *  `Record<never, never>`：无字段，也不给交叉类型引入 `event: never` 的索引约束。 */
export type AssistantMessageStarted = Record<never, never>;

/** 工具调用开始事件。 */
export interface ToolStarted {
  call_id: string;
  tool: string;
  args: Record<string, unknown>;
  purpose: string;
  /** 封套平铺字段（EventEnvelope → data 整包 spread）：回合归属与同回合平局裁决用（R15） */
  turn_id?: string;
  seq?: number;
}

/** 工具失败时的机器可读错误三元组（对齐 web_host ToolCompleted.error；旧事件无此字段）。 */
export interface ToolErrorInfo {
  code: string;
  message: string;
  hint: string;
}

/** 工具调用完成事件（degraded 为降级标记，如 "flyai_unavailable"）。 */
export interface ToolCompleted {
  call_id: string;
  ok: boolean;
  data: unknown;
  degraded?: string | null;
  /** 失败时的错误三元组（如配额用尽 flyai_rate_limited）；成功或旧事件为 null/缺省。 */
  error?: ToolErrorInfo | null;
  source_usage?: Record<string, unknown> | null;
}

/** 阶段迁移事件（Python 侧 from_phase/to_phase 以 alias "from"/"to" 上线）。 */
export interface PhaseTransition {
  from: string;
  to: string;
}

/** 一组快捷作答选项（对应一个可枚举问题）：label 为问题短标签（可空），options 为候选。 */
export interface QuickReplyGroup {
  label: string;
  options: string[];
}

/** 单轮回复完成事件。 */
export interface TurnDone {
  reply_id: string;
  usage: Record<string, unknown>;
  /** 本轮产生的搜索快照 cid（M3 证据链，后端 Task 10 注入；旧回合无此字段）。 */
  snapshot_ids?: string[];
  /** 轮末抽取的多组快捷作答选项（每组对应一个可枚举问题，[[options:标签]] 标记）。
   *  多问时一问一组，前端逐组渲染 chips；无标记/旧事件缺省。 */
  quick_reply_groups?: QuickReplyGroup[];
  /** @deprecated 旧单组扁平选项；仅向后兼容，优先用 quick_reply_groups。 */
  quick_replies?: string[];
  /** 回合未收敛出正文（前导语/触顶/收敛失败）→ 前端展示中断提示与重新生成。 */
  incomplete?: boolean;
}

/** 错误事件（带下一步建议 hint）。 */
export interface ErrorEvent {
  code: string;
  message: string;
  hint: string;
}

/** P0-10：系统提示事件（上限触顶/未生成正文）——不得混进助手正文。
 *  前端渲染为可关闭的中性提示条，与产品说的话在视觉上区分。 */
export interface SystemNotice {
  code: string;
  message: string;
}

// ---------------------------------------------------------------- 信封与判别联合

export type EventType =
  | "assistant_message_started"
  | "message_delta"
  | "tool_started"
  | "tool_completed"
  | "phase_transition"
  | "turn_done"
  | "system_notice"
  | "error";

/**
 * 统一信封（Plan 3 EventEnvelope：turn_id + seq + 事件名 + payload）。
 * 注意：Plan 3 编码时把 turn_id/seq 与 payload 字段平铺在同一 JSON 对象里，
 * 故 data 为解析后的整包（含 turn_id/seq + payload 字段）。
 */
export interface EventEnvelope {
  turn_id: string;
  seq: number;
  event: EventType;
  data: Record<string, unknown>;
}

/** 判别联合：解析后按 event 分派时的强类型视图。 */
export type SseEvent =
  | ({ event: "assistant_message_started" } & AssistantMessageStarted)
  | ({ event: "message_delta" } & MessageDelta)
  | ({ event: "tool_started" } & ToolStarted)
  | ({ event: "tool_completed" } & ToolCompleted)
  | ({ event: "phase_transition" } & PhaseTransition)
  | ({ event: "turn_done" } & TurnDone)
  | ({ event: "system_notice" } & SystemNotice)
  | ({ event: "error" } & ErrorEvent);

/** 回合创建响应（POST /projects/{id}/messages，见 web_host/api/turns.py）。 */
export interface CreateTurnResponse {
  turn_id: string;
  status: string;
}
