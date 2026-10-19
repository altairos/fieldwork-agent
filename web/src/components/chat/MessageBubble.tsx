import { memo } from "react";
import { MarkdownBody } from "./AssistantStream";
import { stripOptionTags } from "../../lib/optionTags";
import { normalizeCjkQuotes } from "../../lib/cjkQuotes";

/**
 * MessageBubble — 时间线单条消息（历史）。
 *
 * 设计板同源：
 *   user lines 436-440：`flex justify-end` 外层 + 气泡
 *     `max-w-[78%] rounded-lg rounded-br-sm px-4 py-2.5 text-[13.5px] leading-relaxed shadow-card`
 *     底色原为深静蓝（bg-indigo + onindigo 反白文字；用户反馈深底过重），
 *     2026-10-19 改浅洗底 indigowash + 墨色文字 ink + indigo/25 描边——与 Chip indigo tone 同族。
 *   assistant lines 451-462：`flex gap-3` + 头像 `w-6 h-6 shrink-0 mt-0.5`（line 452）+
 *     `min-w-0 flex-1` 内嵌宋体 markdown（复用 MarkdownBody，与流式缓冲同一渲染口径）
 *
 * 红线：正文原样呈现（user 用 whitespace-pre-wrap 保留换行；assistant 走 markdown），不改写、不补数字。
 * 例外一：[[options]] 标记渲染前剥离（兜底——后端轮末已剥离，正常历史无标记；防旧数据/异常路径闪烁）。
 * 例外二：assistant 半角引号渲染前归一为全角（问题3前端；覆盖归一上线前的存量历史，
 * user 输入不归一——用户亲手输入的引号原样呈现）。
 */
function MessageBubbleInner({ role, content }: { role: "user" | "assistant"; content: string }) {
  if (role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[78%] rounded-lg rounded-br-sm bg-indigowash text-ink border border-indigo/25 px-4 py-2.5 text-[13.5px] leading-relaxed shadow-card whitespace-pre-wrap break-words">
          {content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex gap-3">
      <img src="/fieldwork-logo-full.svg" alt="" className="w-6 h-6 shrink-0 mt-0.5" />
      <div className="min-w-0 flex-1">
        <MarkdownBody text={normalizeCjkQuotes(stripOptionTags(content))} />
      </div>
    </div>
  );
}

/**
 * memo 包装（性能修复）：props 仅两个字符串，父组件 Timeline 会因为工具事件、
 * 空态布尔、activity 条变化而重渲染；不 memo 时每条历史消息都要重跑一次
 * remarkGfm 解析，成本随会话长度线性叠加。
 */
export const MessageBubble = memo(MessageBubbleInner);

