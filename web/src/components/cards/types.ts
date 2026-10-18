/**
 * 卡片层公共类型 — 单独成模块以打破 `registry.tsx ↔ 各卡片` 的循环类型依赖
 * （卡片 import ToolCardProps、registry import 卡片组件）。
 * `registry.tsx` 原样 re-export，调用方仍可从 registry 取用（brief 契约不变）。
 */

/** 工具失败原因（`Envelope.error` 的 JSON 形态；三字段都可能缺省）。 */
export interface ToolErrorView {
  code?: string;
  message?: string;
  hint?: string;
}

/** 每张工具卡的统一 props：payload 原样透传（形状未知 → 卡内自行容错解析）。 */
export interface ToolCardProps {
  /** `tool_completed.data`：内核 Envelope.data 的 JSON 形态，可能为 null（工具失败/降级）。 */
  data: unknown;
  /** `Envelope.degraded`：降级原因原文（如 "flyai_unavailable"），有值即需在卡头标注。 */
  degraded?: string | null;
  /** `Envelope.error`：失败原因；失败卡据此给出可操作提示而不是泛化空态。 */
  error?: ToolErrorView | null;
}
