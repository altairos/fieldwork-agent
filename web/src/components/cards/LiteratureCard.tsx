import { Chip } from "../ui/Chip";
import { IconBook } from "../ui/icons";
import { CardShell, NoData } from "./CardShell";
import { asArray, asRecord, boolOf, numOf, strOf } from "./data";
import type { ToolCardProps } from "./types";

/**
 * LiteratureCard — 学术文献检索卡（`search_literature` → LiteraturePage）。
 *
 * data 形状（kernel/sources/openalex.py 的 LiteraturePage）：
 *   `{ items: LiteratureItem[], total: number, chinese_sources: {label,url}[] }`，
 *   每条 LiteratureItem 含 id/title/authors/year/doi/venue/cited_by_count/abstract/
 *   open_access_url/is_oa。卡内逐字段如实呈现，空字段整行不渲染、不臆造。
 *
 * 红线（问题2）：旅行类工具查不到学术文献，本卡是文献检索的唯一同源呈现——
 *   DOI 拼 `https://doi.org/<裸DOI>`、OA 全文取 open_access_url 原样；
 *   OpenAlex 以外文为主，底部呈现内核给的中文社科深链（国家哲社文献中心 / PubScholar），
 *   链接原样可点击，不转述、不编造中文命中。
 * 无数据（工具失败/零命中）走 NoData；零命中但带中文深链时仍呈现深链供用户转检。
 */

/** 单条文献的容错读法（data 为 unknown，逐字段经 data.ts 读函数取值）。 */
function toItems(raw: unknown): Record<string, unknown>[] {
  return asArray(raw).map(asRecord);
}

function toSources(raw: unknown): { label: string; url: string }[] {
  return asArray(raw)
    .map(asRecord)
    .map((s) => ({ label: strOf(s.label), url: strOf(s.url) }))
    .filter((s) => s.label && s.url);
}

export function LiteratureCard({ data, degraded }: ToolCardProps) {
  const rec = asRecord(data);
  const items = toItems(rec.items);
  const total = numOf(rec.total) ?? items.length;
  const sources = toSources(rec.chinese_sources);
  const statusChip =
    items.length > 0 ? <Chip tone="neutral">{`命中 ${total}`}</Chip> : undefined;

  return (
    <CardShell
      title="文献检索"
      icon={<IconBook className="w-4 h-4" />}
      degraded={degraded}
      statusChip={statusChip}
    >
      {items.length === 0 ? (
        <NoData hint="未取得文献；可重试 search_literature（建议英文主题词），或用下方中文社科平台深链检索。" />
      ) : (
        <div className="space-y-2">
          {items.map((it, i) => {
            const title = strOf(it.title);
            const authors = asArray(it.authors).map(strOf).filter((a) => a !== "");
            const year = numOf(it.year);
            const venue = strOf(it.venue);
            const doi = strOf(it.doi);
            const cited = numOf(it.cited_by_count);
            const abstract = strOf(it.abstract);
            const oaUrl = it.open_access_url == null ? "" : strOf(it.open_access_url);
            const isOa = boolOf(it.is_oa);
            const key = strOf(it.id) || `${title}-${i}`;
            return (
              <div key={key} className="hairline rounded-lg bg-card p-3">
                {/* 标题 */}
                {title && <div className="text-[13px] font-medium text-ink">{title}</div>}

                {/* 作者 · 年份 · 期刊（空字段不渲染） */}
                {(authors.length > 0 || year !== null || venue) && (
                  <div className="mt-1 flex flex-wrap items-center gap-x-1.5 text-[11.5px] text-inkfaint">
                    {authors.length > 0 && <span>{authors.join("、")}</span>}
                    {year !== null && <span>· {year}</span>}
                    {venue && <span>· {venue}</span>}
                  </div>
                )}

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

                {/* 摘要（重建自 inverted index，原样呈现） */}
                {abstract && (
                  <p className="mt-1.5 text-[12px] leading-relaxed text-inksoft">{abstract}</p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* 中文社科检索深链（OpenAlex 以外文为主，中文期刊转这些平台；链接原样可点击） */}
      {sources.length > 0 && (
        <div className="mt-3 border-t border-hairline pt-2.5">
          <div className="text-[10.5px] tracking-[.16em] text-inkfaint mb-1.5">中文社科检索</div>
          <div className="flex flex-wrap gap-2">
            {sources.map((s) => (
              <a
                key={s.url}
                className="chip hairline bg-card text-inksoft hover:bg-cardsunk hover:text-ink transition"
                href={s.url}
                target="_blank"
                rel="noreferrer"
              >
                {s.label}
              </a>
            ))}
          </div>
        </div>
      )}
    </CardShell>
  );
}
