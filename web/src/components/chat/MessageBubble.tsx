import { MarkdownBody } from "./AssistantStream";
import { stripOptionTags } from "../../lib/optionTags";

/**
 * MessageBubble — 时间线单条消息（历史）。
 *
 * 设计板同源：
 *   user lines 436-440：`flex justify-end` 外层 + 气泡
 *     `max-w-[78%] rounded-lg rounded-br-sm bg-indigo text-onindigo px-4 py-2.5
 *      text-[13.5px] leading-relaxed shadow-card`
 *     （设计板 line 437 的 token 外用值 #EDF1F4 已主题化为 onindigo —— 靛蓝底上的气泡文字色）
 *   assistant lines 451-462：`flex gap-3` + 头像 `w-6 h-6 shrink-0 mt-0.5`（line 452）+
 *     `min-w-0 flex-1` 内嵌宋体 markdown（复用 MarkdownBody，与流式缓冲同一渲染口径）
 *
 * 红线：正文原样呈现（user 用 whitespace-pre-wrap 保留换行；assistant 走 markdown），不改写、不补数字。
 * 例外：[[options]] 标记渲染前剥离（兜底——后端轮末已剥离，正常历史无标记；防旧数据/异常路径闪烁）。
 */
export function MessageBubble({ role, content }: { role: "user" | "assistant"; content: string }) {
  if (role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[78%] rounded-lg rounded-br-sm bg-indigo text-onindigo px-4 py-2.5 text-[13.5px] leading-relaxed shadow-card whitespace-pre-wrap break-words">
          {content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex gap-3">
      <img src="/fieldwork-logo-full.svg" alt="" className="w-6 h-6 shrink-0 mt-0.5" />
      <div className="min-w-0 flex-1">
        <MarkdownBody text={stripOptionTags(content)} />
      </div>
    </div>
  );
}
