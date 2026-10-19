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

/** 工具调用开始事件。 */
export interface ToolStarted {
  call_id: string;
  tool: string;
  args: Record<string, unknown>;
  purpose: string;
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

/** 单轮回复完成事件。 */
export interface TurnDone {
  reply_id: string;
  usage: Record<string, unknown>;
  /** 本轮产生的搜索快照 cid（M3 证据链，后端 Task 10 注入；旧回合无此字段）。 */
  snapshot_ids?: string[];
  /** 轮末抽取的快捷作答选项（[[options]] 标记，B 节问题 4；无标记/旧事件缺省）。 */
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

// ---------------------------------------------------------------- 信封与判别联合

export type EventType =
  | "message_delta"
  | "tool_started"
  | "tool_completed"
  | "phase_transition"
  | "turn_done"
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
  | ({ event: "message_delta" } & MessageDelta)
  | ({ event: "tool_started" } & ToolStarted)
  | ({ event: "tool_completed" } & ToolCompleted)
  | ({ event: "phase_transition" } & PhaseTransition)
  | ({ event: "turn_done" } & TurnDone)
  | ({ event: "error" } & ErrorEvent);

/** 回合创建响应（POST /projects/{id}/messages，见 web_host/api/turns.py）。 */
export interface CreateTurnResponse {
  turn_id: string;
  status: string;
}
