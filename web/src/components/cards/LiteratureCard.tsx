import { IconSpark } from "../ui/icons";
import { CardShell, NoData } from "./CardShell";
import { asArray, asRecord, numOf, strOf } from "./data";
import type { ToolCardProps } from "./types";

/** 文献候选与检索记录；数据库潜在命中数始终与实际条目数分开显示。 */
export function LiteratureCard({ data, degraded, error }: ToolCardProps) {
  const rec = asRecord(data);
  const items = asArray(rec.items).map(asRecord);
  const reports = asArray(rec.query_reports).map(asRecord);
  const sources = asArray(rec.chinese_sources).map(asRecord);

  return (
    <CardShell
      title="文献检索"
      subtitle={`返回 ${items.length} 篇候选 · 标题摘要初筛`}
      icon={<IconSpark className="w-4 h-4" />}
      degraded={degraded}
      open={false}
    >
      {error?.message && <p className="text-[12px] text-ochre mb-2">{error.message}</p>}
      {items.length === 0 ? (
        <NoData hint="本次未取得文献候选，可调整核心概念或使用下方中文平台入口补检。" />
      ) : (
        <ol className="space-y-3">
          {items.map((item, index) => {
            const doi = strOf(item.doi);
            const id = strOf(item.id);
            const url = doi ? `https://doi.org/${encodeURI(doi)}` : id;
            const safeUrl = /^https:\/\/(doi\.org|openalex\.org)\//i.test(url) ? url : undefined;
            const authors = asArray(item.authors).map((a) => strOf(a)).filter(Boolean).join("、");
            const year = numOf(item.year);
            return (
              <li key={`${id || doi || strOf(item.title)}-${index}`} className="text-[12px] border-b border-hairsoft pb-2">
                <div className="text-inksoft font-medium">
                  {safeUrl ? (
                    <a href={safeUrl} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">
                      {strOf(item.title) || "未提供题名"}
                    </a>
                  ) : (strOf(item.title) || "未提供题名")}
                </div>
                <p className="text-inkfaint mt-1">{[authors, year, strOf(item.venue)].filter((v) => v !== "" && v !== null).join(" · ")}</p>
                <p className="text-inkfaint mt-1">{asArray(item.matched_queries).map((q) => strOf(q)).join(" / ")}</p>
                {!strOf(item.abstract) && <p className="text-ochre mt-1">无摘要 · 仅题名初筛，待核验</p>}
                {strOf(item.abstract) && (
                  <details className="mt-1 text-inkfaint">
                    <summary className="cursor-pointer">查看摘要</summary>
                    <p className="mt-1 leading-relaxed whitespace-pre-wrap">{strOf(item.abstract)}</p>
                  </details>
                )}
              </li>
            );
          })}
        </ol>
      )}
      {reports.length > 0 && (
        <details className="mt-3 text-[11px] text-inkfaint">
          <summary className="cursor-pointer">检索式与每路数据库命中（可能重叠）</summary>
          {reports.map((report, index) => (
            <div key={`${strOf(report.label)}-${index}`} className="mt-2 space-y-1">
              <p>{strOf(report.label)} · 数据库命中 {numOf(report.matched_total) ?? "未知"} · 取回 {numOf(report.retrieved_count) ?? 0} 篇</p>
              <p className="break-words">{strOf(report.query)}</p>
              <p>{strOf(report.next_action)}</p>
            </div>
          ))}
          <p className="mt-2">命中数不代表已阅读或已筛选的数量；本次为小批候选检索。</p>
        </details>
      )}
      {sources.length > 0 && (
        <div className="mt-3 text-[11px] text-inkfaint space-y-1">
          <p>中文平台补检入口（尚未核验命中）</p>
          {sources.map((source, index) => {
            const url = strOf(source.url);
            return /^https:\/\/(www\.ncpssd\.cn|pubscholar\.cn)\//i.test(url) ? (
              <a key={`${url}-${index}`} href={url} target="_blank" rel="noopener noreferrer" className="block underline underline-offset-2">
                {strOf(source.label)}
              </a>
            ) : null;
          })}
        </div>
      )}
    </CardShell>
  );
}
