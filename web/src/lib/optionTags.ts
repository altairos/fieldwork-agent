/**
 * [[options]] 快捷作答标记处理 — 前端剥离侧（多组带标签）。
 *
 * 协议：LLM 每个可枚举提问在回复末尾输出一组 `[[options:标签]]选项甲|选项乙[[/options]]`
 * （标签可选，无标签即旧单组形式）；后端 `loop.py _extract_quick_replies` 轮末剥离后
 * 才入史，故持久化正文本无标记。本模块服务于两处流式/兜底场景：
 *   - AssistantStream：message_delta 已流出标记文本（含半截标记），渲染前剥离防闪烁；
 *   - MessageBubble：历史消息兜底（正常均为后端已剥离的干净正文）。
 * 剥离语义与后端严格对齐（_OPTIONS_TAG_RE.sub("", reply).rstrip()）：删除所有完整
 * 标记块、保留块外正文并去尾部空白；流式未闭合的半截标记从起始处截断。
 */

/** 完整标记段（全局、非贪婪；容许可选 :标签）——与后端 finditer/sub 行为一致。 */
const OPTIONS_FULL_RE = /\[\[options(?::[^\]]*)?\]\][\s\S]*?\[\[\/options\]\]/g;

/** 残留的（半截）起始标记：流式中途只有开头、尚未闭合。 */
const OPTIONS_OPEN_RE = /\[\[options/;

/** 删除所有 [[options[:标签]]] 标记段：完整块全删、保留两侧正文；半截块从起始截断；无标记原样。 */
export function stripOptionTags(text: string): string {
  let out = text.replace(OPTIONS_FULL_RE, "");
  const open = out.search(OPTIONS_OPEN_RE);
  if (open !== -1) out = out.slice(0, open);
  return out.replace(/\s+$/, "");
}
