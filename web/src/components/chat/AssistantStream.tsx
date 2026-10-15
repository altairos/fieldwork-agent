import Markdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { useConversationStore } from "../../stores/conversation";

/**
 * AssistantStream — 助手流式正文（打字机）；同文件导出 MarkdownBody 供 MessageBubble 复用
 * （同一套 markdown 渲染口径，避免中栏出现两种正文样式；不新增 brief 文件清单外的第 7 个文件）。
 *
 * 设计板同源：中栏助手 markdown 流 lines 450-462
 *   头像位 line 452 `w-6 h-6 shrink-0 mt-0.5`（品牌标记 `/miovelle-mark-cinnabar.svg`，public 根目录）
 *   外层 line 451 `flex gap-3` + line 453 `min-w-0 flex-1`
 *   正文 line 454 `font-serif text-[14.5px] leading-[1.85] text-ink space-y-2.5`
 *   列表 line 456 `space-y-1 text-[13.5px] text-inksoft list-none` +
 *        line 457 每项 `flex gap-2` 前置朱砂点 `<span class="text-cinnabar">·</span>`
 *   强调 line 455/457 `<b class="text-ink">` → `strong` 映射
 *   链接色沿用 tokens indigo（line 568 `text-indigo` 同源用法）
 *
 * 打字机效果 = streamBuffer 增量追加即重渲染（message_delta 由 conversation store 归约，Task 3）；
 * turnRunning 时尾部挂朱砂光标脉冲（animate-pulse，与 PulseDot 进行中态同一动效语义）。
 * 红线：只渲染内核下发的文本，不改写、不补数字。
 */

/** markdown 元素映射（设计板 lines 454-461 的宋体正文 + 朱砂点列表；表格/代码为 gfm 兜底样式）。 */
const MD_COMPONENTS: Components = {
  p: ({ children }) => <p className="whitespace-pre-wrap">{children}</p>,
  ul: ({ children }) => <ul className="space-y-1 text-[13.5px] text-inksoft list-none pl-0">{children}</ul>,
  ol: ({ children }) => <ol className="space-y-1 text-[13.5px] text-inksoft list-none pl-0">{children}</ol>,
  li: ({ children }) => (
    <li className="flex gap-2">
      <span className="text-cinnabar shrink-0" aria-hidden="true">
        ·
      </span>
      <span className="min-w-0 flex-1 whitespace-pre-wrap">{children}</span>
    </li>
  ),
  strong: ({ children }) => <strong className="text-ink font-semibold">{children}</strong>,
  a: ({ children, href }) => (
    <a href={href} className="text-indigo hover:underline" target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
  h1: ({ children }) => <h1 className="font-serif text-[17px] font-semibold text-ink">{children}</h1>,
  h2: ({ children }) => <h2 className="font-serif text-[15.5px] font-semibold text-ink">{children}</h2>,
  h3: ({ children }) => <h3 className="font-serif text-[14.5px] font-semibold text-ink">{children}</h3>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-cinnabar pl-3 text-inksoft">{children}</blockquote>
  ),
  code: ({ children }) => (
    <code className="num text-[13px] bg-cardsunk hairline rounded px-1 py-0.5">{children}</code>
  ),
  pre: ({ children }) => (
    <pre className="hairline rounded-md bg-cardsunk p-3 overflow-x-auto scroll text-[12.5px]">{children}</pre>
  ),
  hr: () => <hr className="rule border-0 my-3" />,
  table: ({ children }) => (
    <table className="w-full text-[12.5px] border-collapse hairline rounded-md">{children}</table>
  ),
  th: ({ children }) => (
    <th className="bg-cardsunk text-inksoft font-medium text-left px-2 py-1.5 border-b border-hairline">
      {children}
    </th>
  ),
  td: ({ children }) => <td className="px-2 py-1.5 border-b border-hairsoft text-inksoft">{children}</td>,
};

/** 宋体 markdown 正文（历史助手消息与流式缓冲共用）。 */
export function MarkdownBody({ text }: { text: string }) {
  return (
    <div className="font-serif text-[14.5px] leading-[1.85] text-ink space-y-2.5 break-words">
      <Markdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
        {text}
      </Markdown>
    </div>
  );
}

/** 当前回合的助手流式正文；无缓冲且未运行时不渲染（不占时间线空间）。 */
export function AssistantStream() {
  const buffer = useConversationStore((s) => s.streamBuffer);
  const running = useConversationStore((s) => s.turnRunning);
  if (!buffer && !running) return null;
  return (
    <div className="flex gap-3">
      <img src="/miovelle-mark-cinnabar.svg" alt="" className="w-6 h-6 shrink-0 mt-0.5" />
      <div className="min-w-0 flex-1">
        {buffer ? (
          <MarkdownBody text={buffer} />
        ) : (
          <div className="text-[12.5px] text-inkfaint">助手正在组织回复…</div>
        )}
        {running && (
          <span
            className="inline-block w-1.5 h-4 align-text-bottom ml-0.5 bg-cinnabar/70 animate-pulse"
            aria-hidden="true"
          />
        )}
      </div>
    </div>
  );
}
