import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CardShell, NoData } from "./CardShell";
import { asRecord, numOf, strOf } from "./data";
import type { ToolCardProps } from "./types";

/**
 * AiSearchCard — search_ai（AI 语义检索）专属卡（spec A3）。
 * ai-search 返回 Markdown 直答文本（ItemPage.answer，R1）而非条目列表；
 * 旧 PoiCard 按 items 渲染 → 答案落 NoData。渲染管线对照 AssistantStream
 * （react-markdown + remarkGfm，容器不加 pre-wrap）。红线：答案原样呈现，不加工。
 */
export function AiSearchCard({ data, degraded }: ToolCardProps) {
  const rec = asRecord(data);
  const answer = strOf(rec.answer).trim();
  const total = numOf(rec.total) ?? 0;
  return (
    <CardShell title="AI 检索" subtitle={answer ? "" : total > 0 ? `${total} 条` : undefined}
               degraded={degraded}>
      {answer ? (
        <div className="text-[12.5px] leading-relaxed text-ink break-words">
          <Markdown remarkPlugins={[remarkGfm]}>{answer}</Markdown>
        </div>
      ) : (
        <NoData hint="本次 AI 检索未返回答案文本。" />
      )}
    </CardShell>
  );
}
