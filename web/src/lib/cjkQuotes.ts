/**
 * 中文全角引号归一 — 问题3的前端渲染侧。
 *
 * 后端 `loop.py _normalize_cjk_quotes` 轮末把成对半角双引号（内容含中文）
 * 归一为全角后才入史；本模块覆盖它管不到的两处：
 *   - AssistantStream：流式缓冲（message_delta 直发，未经轮末归一）；
 *   - MessageBubble：归一上线前的存量历史消息。
 * 规则与后端严格对齐：成对、同行、无嵌套的半角双引号，内容含 CJK 才转
 * 全角“”；纯西文内容保持半角；代码块（``` 围栏内的奇数段）不动。
 */

/** 成对同行半角双引号（与后端 _HALF_QUOTED_RE 一致）。 */
const HALF_QUOTED_RE = /"([^"\n]*)"/g;

/** CJK 汉字（与后端 _CJK_CHAR_RE 一致：基本区 + 扩展A）。 */
const CJK_CHAR_RE = /[\u4e00-\u9fff\u3400-\u4dbf]/;

/** 成对半角双引号内容含中文时转全角“”，否则原样；代码块段不动。 */
export function normalizeCjkQuotes(text: string): string {
  const parts = text.split("```");
  for (let i = 0; i < parts.length; i += 2) { // 偶数段在代码块外
    parts[i] = parts[i].replace(
      HALF_QUOTED_RE,
      (m, inner: string) => (CJK_CHAR_RE.test(inner) ? `“${inner}”` : m));
  }
  return parts.join("```");
}
