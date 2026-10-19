import { Chip } from "../ui/Chip";
import { IconBook } from "../ui/icons";
import { CardShell, NoData } from "./CardShell";
import { asArray, asRecord, boolOf, numOf, strOf } from "./data";
import type { ToolCardProps } from "./types";

/**
 * LiteratureCard — 学术文献检索卡（`search_literature` → LiteraturePage）。
 *
 * data 形状（kernel/sources/openalex.py 的 LiteraturePage）：
 *   `{ items: LiteratureItem[], total: number, query_reports: [...],
 *      chinese_sources: {label,url}[] }`，每条 LiteratureItem 含 id/title/authors/
 *   year/doi/venue/cited_by_count/abstract/open_access_url/is_oa/matched_queries。
 *   卡内逐字段如实呈现，空字段整行不渲染、不臆造；数据库潜在命中数
 *   （total / query_reports.matched_total）始终与实际返回条目数分开显示。
 *
 * 红线（问题2）：旅行类工具查不到学术文献，本卡是文献检索的唯一同源呈现——
 *   DOI 拼 `https://doi.org/<裸DOI>`、OA 全文取 open_access_url 原样；
 *   OpenAlex 以外文为主，底部呈现内核给的中文社科深链（国家哲社文献中心 / PubScholar）。
 * 标题链接走白名单（doi.org/openalex.org）、中文深链走白名单
 *   （ncpssd.cn/pubscholar.cn）——非学术跳转（如 javascript:）不渲染为链接。
 * 无数据（工具失败/零命中）走 NoData；零命中但带中文深链时仍呈现深链供用户转检。
 */

export function LiteratureCard({ data, degraded, error }: ToolCardProps) {
  const rec = asRecord(data);
  const items = asArray(rec.items).map(asRecord);
  const reports = asArray(rec.query_reports).map(asRecord);
  const total = numOf(rec.total) ?? items.length;
  const sources = asArray(rec.chinese_sources).map(asRecord);
  const statusChip =
    items.length > 0 ? <Chip tone="neutral">{`命中 ${total}`}</Chip> : undefined;

  return (
    <CardShell
      title="文献检索"
      subtitle={`返回 ${items.length} 篇候选 · 标题摘要初筛`}
      icon={<IconBook className="w-4 h-4" />}
      degraded={degraded}
      statusChip={statusChip}
      open={false}
    >
      {error?.message && <p className="text-[12px] text-ochre mb-2">{error.message}</p>}
      {items.length === 0 ? (
        <NoData hint="未取得文献；可重试 search_literature（建议英文主题词），或用下方中文社科平台深链检索。" />
      ) : (
        <div className="space-y-2">
          {items.map((item, index) => {
            const title = strOf(item.title);
            const authors = asArray(item.authors).map((a) => strOf(a)).filter(Boolean).join("、");
            const year = numOf(item.year);
            const venue = strOf(item.venue);
            const doi = strOf(item.doi);
            const id = strOf(item.id);
            const cited = numOf(item.cited_by_count);
            const abstract = strOf(item.abstract);
            const oaUrl = item.open_access_url == null ? "" : strOf(item.open_access_url);
            const isOa = boolOf(item.is_oa);
            const matched = asArray(item.matched_queries).map((q) => strOf(q)).join(" / ");
            const url = doi ? `https://doi.org/${encodeURI(doi)}` : id;
            const safeUrl = /^https:\/\/(doi\.org|openalex\.org)\//i.test(url) ? url : undefined;
            const key = id || doi || `${title}-${index}`;
            return (
              <div key={key} className="hairline rounded-lg bg-card p-3">
                {/* 标题（白名单链接：doi.org/openalex.org；非学术跳转不渲染为链接） */}
                <div className="text-[13px] font-medium text-ink">
                  {safeUrl ? (
                    <a href={safeUrl} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">
                      {title || "未提供题名"}
                    </a>
                  ) : (title || "未提供题名")}
                </div>

                {/* 作者 · 年份 · 期刊（空字段不渲染） */}
                {(authors !== "" || year !== null || venue) && (
                  <div className="mt-1 flex flex-wrap items-center gap-x-1.5 text-[11.5px] text-inkfaint">
                    {authors !== "" && <span>{authors}</span>}
                    {year !== null && <span>· {year}</span>}
                    {venue && <span>· {venue}</span>}
                  </div>
                )}
                {matched && <p className="mt-1 text-[11.5px] text-inkfaint">{matched}</p>}

                {/* 被引 + OA 标记 + DOI/全文链接 */}
                <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px]">
                  {cited !== null && <span className="text-inkfaint">被引 {cited}</span>}
                  {isOa && <Chip tone="bamboo">开放获取</Chip>}
                  {doi && (
                    <a
                      className="text-cinnabar underline decoration-cinnabar/40 underline-offset-2 hover:decoration-cinnabar"
                      href={`https://doi.org/${doi}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      DOI
                    </a>
                  )}
                  {oaUrl && (
                    <a
                      className="text-cinnabar underline decoration-cinnabar/40 underline-offset-2 hover:decoration-cinnabar"
                      href={oaUrl}
                      target="_blank"
                      rel="noreferrer"
                    >
                      开放获取全文
                    </a>
                  )}
                </div>

                {/* 摘要（重建自 inverted index，原样呈现）；无摘要如实标注仅题名初筛 */}
                {abstract && (
                  <p className="mt-1.5 text-[12px] leading-relaxed text-inksoft">{abstract}</p>
                )}
                {!abstract && <p className="mt-1.5 text-[11px] text-ochre">无摘要 · 仅题名初筛，待核验</p>}
              </div>
            );
          })}
        </div>
      )}

      {/* 检索式与每路数据库命中（命中数与已读数分开，可追溯） */}
      {reports.length > 0 && (
        <details className="mt-3 text-[11px] text-inkfaint">
          <summary className="cursor-pointer">检索式与每路数据库命中（可能重叠）</summary>
          {reports.map((report, idx) => (
            <div key={`${strOf(report.label)}-${idx}`} className="mt-2 space-y-1">
              <p>{strOf(report.label)} · 数据库命中 {numOf(report.matched_total) ?? "未知"} · 取回 {numOf(report.retrieved_count) ?? 0} 篇</p>
              <p className="break-words">{strOf(report.query)}</p>
              <p>{strOf(report.next_action)}</p>
            </div>
          ))}
          <p className="mt-2">命中数不代表已阅读或已筛选的数量；本次为小批候选检索。</p>
        </details>
      )}

      {/* 中文社科检索深链（OpenAlex 以外文为主；白名单内链接原样可点击） */}
      {sources.length > 0 && (
        <div className="mt-3 border-t border-hairline pt-2.5">
          <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-1.5">中文社科检索</div>
          <div className="flex flex-wrap gap-2">
            {sources.map((s, i) => {
              const sUrl = strOf(s.url);
              return /^https:\/\/(www\.ncpssd\.cn|pubscholar\.cn)\//i.test(sUrl) ? (
                <a
                  key={`${sUrl}-${i}`}
                  className="chip hairline bg-card text-inksoft hover:bg-cardsunk hover:text-ink transition"
                  href={sUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  {strOf(s.label)}
                </a>
              ) : null;
            })}
          </div>
        </div>
      )}
    </CardShell>
  );
}

