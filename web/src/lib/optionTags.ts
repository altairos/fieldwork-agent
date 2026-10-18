/**
 * [[options]] 快捷作答标记处理 — B 节问题 4 的前端剥离侧。
 *
 * 协议：LLM 枚举型提问在回复末尾输出 `[[options]]选项甲|选项乙[[/options]]`；
 * 后端 `loop.py _extract_quick_replies` 轮末剥离后才入史，故持久化正文本无标记。
 * 本模块服务于两处流式/兜底场景：
 *   - AssistantStream：message_delta 已流出标记文本（含半截标记），渲染前剥离防闪烁；
 *   - MessageBubble：历史消息兜底（正常均为后端已剥离的干净正文）。
 * 剥离语义与后端严格对齐：标记前的正文保留并去尾部空白，标记及其后内容丢弃
 * （协议约定标记在最末尾；即便模型违约居中输出，两侧剥离结果仍一致）。
 */

/** 完整标记段（首个匹配，无 g 标志——与后端 re.search 行为一致）。 */
const OPTIONS_FULL_RE = /\[\[options\]\][\s\S]*?\[\[\/options\]\]/;

/** 删除 [[options]] 标记段：完整标记删至闭合；半截标记（流式中途只有起始）截断起始处；无标记原样返回。 */
export function stripOptionTags(text: string): string {
  const full = OPTIONS_FULL_RE.exec(text);
  if (full) return text.slice(0, full.index).replace(/\s+$/, "");
  const open = text.indexOf("[[options]]");
  if (open !== -1) return text.slice(0, open).replace(/\s+$/, "");
  return text;
}
