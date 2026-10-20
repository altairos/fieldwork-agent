import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * ProposalViewer — 提案正文渲染（react-markdown + remark-gfm，宋体纸面阅读排版）。
 * 设计板同源：view-proposal 正文区（lines 712-767）：
 *   纸面容器 `max-w-[720px] mx-auto px-10 py-10`（line 714；移动端 px-5 lg:px-10，批次 A）
 *   居中题头 kicker `text-[11px] tracking-[.24em] text-cinnabar`（line 716）
 *   h1 居中大标题 `font-serif text-[30px] font-700 leading-snug`（line 717，700 → Tailwind font-bold）
 *   h2 章节标题 `font-serif text-[21px] font-600`（lines 725/743，600 → font-semibold；编号由 markdown 原文携带）
 *   正文 p `font-serif text-[15px] leading-[1.95]`（lines 727-728）
 *   预算表 hairline 圆角 + thead bg-cardsunk + 行 border-t border-hairsoft（lines 744-753）
 *   访谈提纲引用块 `border-l-2 border-cinnabar bg-cinnabarwash/40 rounded-r-md`（lines 731-739；
 *     块内段落缩小为 13.5px/1.9 见 tokens.css `.proposal-body blockquote p`）
 *   行内代码 `num text-indigo`（line 755）；分隔 `rule`（lines 722/758）
 *   页脚 `text-center text-[11.5px] text-inkfaint` + logo（lines 759-761）
 * kind/version 仅用于题头 meta 行（数据来自 GET /projects/{id}/proposal）。
 */
const COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="font-serif text-[30px] font-bold leading-snug text-center text-ink mb-3">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="font-serif text-[21px] font-semibold text-ink mt-10 mb-4 first:mt-0">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="font-serif text-[17px] font-semibold text-ink mt-6 mb-3">{children}</h3>
  ),
  p: ({ children }) => (
    <p className="font-serif text-[15px] leading-[1.95] text-inksoft mb-4">{children}</p>
  ),
  strong: ({ children }) => <strong className="text-ink font-semibold">{children}</strong>,
  ul: ({ children }) => (
    <ul className="list-disc pl-6 mb-4 space-y-1 font-serif text-[15px] leading-[1.95] text-inksoft marker:text-inkfaint">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal pl-6 mb-4 space-y-1 font-serif text-[15px] leading-[1.95] text-inksoft marker:text-inkfaint num">
      {children}
    </ol>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-cinnabar bg-cinnabarwash/40 pl-4 pr-3 py-3 my-5 rounded-r-md font-serif text-[13.5px] leading-[1.9] text-inksoft">
      {children}
    </blockquote>
  ),
  table: ({ children }) => (
    <table className="w-full text-[13px] hairline rounded-lg overflow-hidden mb-5 border-collapse">{children}</table>
  ),
  thead: ({ children }) => <thead className="bg-cardsunk text-inksoft">{children}</thead>,
  th: ({ children }) => <th className="text-left px-4 py-2.5 font-medium">{children}</th>,
  td: ({ children }) => <td className="px-4 py-2.5 border-t border-hairsoft font-serif align-top">{children}</td>,
  code: ({ children, className }) =>
    // 代码块内（pre > code）不带行内样式，行内代码走 num + indigo（line 755）
    className?.includes("language-") ? (
      <code className={className}>{children}</code>
    ) : (
      <code className="num text-indigo text-[.92em]">{children}</code>
    ),
  pre: ({ children }) => (
    <pre className="hairline rounded-md bg-cardsunk p-3 overflow-x-auto scroll mb-4 text-[12.5px] leading-relaxed">
      {children}
    </pre>
  ),
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer"
       className="text-indigo underline decoration-indigo/30 hover:decoration-indigo">
      {children}
    </a>
  ),
  hr: () => <div className="rule my-8" />,
};

export function ProposalViewer({
  markdown,
  kind,
  version,
}: {
  markdown: string;
  /** proposal | interview_guide */
  kind: string;
  /** draft | final */
  version: string;
}) {
  const kicker =
    kind === "interview_guide" ? "INTERVIEW GUIDE · 访谈提纲" : "RESEARCH PROPOSAL · 研究方案";
  return (
    <article className="max-w-[720px] mx-auto px-5 lg:px-10 py-10">
      {/* 居中题头（设计板 715-720；kicker 展示 kind/version meta，大标题由 markdown h1 承担） */}
      <div className="text-center mb-2">
        <div className="text-[11px] tracking-[.24em] text-cinnabar num">
          {kicker} · {version.toUpperCase()}
        </div>
      </div>
      <div className="proposal-body">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
          {markdown}
        </ReactMarkdown>
      </div>
      <div className="rule my-8" />
      <div className="text-center text-[11.5px] text-inkfaint">
        <img src={`${import.meta.env.BASE_URL}fieldwork-logo-full.svg`} alt="" className="w-5 h-5 mr-2 align-middle inline-block" />
        田野罗盘 · 结构化田野数据辅助生成的方案草稿 · Markdown 为唯一事实源
      </div>
    </article>
  );
}
