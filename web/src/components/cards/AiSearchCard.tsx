import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CardShell, NoData } from "./CardShell";
import { asRecord, numOf, strOf, toItemPage } from "./data";
import { PoiCard } from "./PoiCard";
import type { ToolCardProps } from "./types";

/**
 * AiSearchCard — search_ai（AI 语义检索）专属卡（spec A3）。
 * ai-search 返回 Markdown 直答文本（ItemPage.answer，R1）而非条目列表；
 * 旧 PoiCard 按 items 渲染 → 答案落 NoData。渲染管线对照 AssistantStream
 * （react-markdown + remarkGfm，容器不加 pre-wrap）。红线：答案原样呈现，不加工。
 * kernel T1 的 `_page_envelope` 保留双解析路径（data 字符串 → answer；
 * data dict → journeys/itemList → items）——dict 形态无 answer 有条目时
 * 回退 PoiCard（终审 W2，A3/A4），改绑前可见的条目不得改绑后落 NoData。
 */
export function AiSearchCard(props: ToolCardProps) {
  const { data, degraded } = props;
  const rec = asRecord(data);
  const answer = strOf(rec.answer).trim();
  const items = toItemPage(data).items;
  // dict 形态（itemList 归一化 ItemPage）无 answer 但有 items → 复用 PoiCard
  // 同构列表渲染，不落 NoData（终审 W2：展示回归修复）
  if (!answer && items.length > 0) return <PoiCard {...props} />;
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
