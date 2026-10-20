import type { ReactNode } from "react";
import type { SearchItem } from "../../types/models";
import { Chip } from "../ui/Chip";
import { EmptyState } from "../ui/EmptyState";
import { Price } from "../ui/Price";
import { CardShell, Notice, RawFields } from "./CardShell";
import { asRecord, LIST_LIMIT, strOf, toItemPage, yuan } from "./data";
import { itemDigest } from "./fields";
import type { ToolCardProps } from "./types";
import { travelDetails } from "./travelDetails";

/**
 * SearchResultCard — ItemPage 形状（8 个飞猪搜索原语的统一返回）的通用结果卡。
 * PoiCard / HotelCard / FlightCard / TrainCard 是它的四个具名配置（标题/图标/无供给文案），
 * 避免四份近似重复的列表渲染；`SearchItemRow` / `ItemList` 另供 SiteProfileCard 复用。
 * 本文件不进注册表（注册表只映射具名卡片）。
 *
 * 设计板同源：右栏交通/住宿条目行 lines 566-575（图标位 `w-8 h-8 rounded-md bg-cardsunk
 * grid place-items-center` + 主行 `text-[12.5px] truncate` + 副行 `text-[10.5px] text-inkfaint`
 * （line 568「直飞 55min · search」）+ 右侧价格 num）；住宿缩略图位 lines 583-585
 * （`¥150/晚`，`/晚` 用 `text-[9.5px] text-inkfaint`）。
 *
 * 副行文案走 `fields.itemDigest`（语义化，不再直接打英文键名与佣金等内部字段）；
 * 未归入摘要的原始字段收在列表底部「更多原始字段」折叠里，数据不丢。
 *
 * 紧凑化（用户反馈）：行距 space-y-2→1.5、缩略图 40→36px、图标位 32→28px、
 * 摘要脚注 mt-3/pt-3→mt-2/pt-2 —— 五条结果少占约 40px 高。
 *
 * 价格不失真（红线③④）：逐条价格一律走 `Price`（null → "暂无报价"，有值 → num ¥ + 来源 chip）；
 * 最低/最高摘要为**同一 payload 数字的复合文本行**（不换算、不补 0），null 同样落"暂无报价"。
 */

/** 单条搜索结果行（图标位 + 名称/副行 + 价格）。 */
export function SearchItemRow({
  item,
  icon,
  thumb = false,
  source = "search",
  unitSuffix,
}: {
  item: SearchItem;
  icon?: ReactNode;
  thumb?: boolean;
  source?: string;
  unitSuffix?: string;
}) {
  const digest = itemDigest(item);
  const travel = travelDetails(item);
  const name = item.name || digest.title || "未命名条目";
  return (
    <li className="flex items-center gap-2">
      {thumb &&
        (item.pic_url ? (
          <img
            src={item.pic_url}
            alt=""
            loading="lazy"
            className="w-9 h-9 rounded-md object-cover bg-cardsunk hairline shrink-0"
          />
        ) : (
          <span className="w-7 h-7 rounded-md bg-cardsunk grid place-items-center shrink-0 text-inkfaint">
            {icon}
          </span>
        ))}
      <div className="flex-1 min-w-0">
        <div className="text-[12.5px] text-ink truncate">
          {item.jump_url ? (
            <a
              href={item.jump_url}
              target="_blank"
              rel="noreferrer"
              className="text-indigo hover:underline"
            >
              {name}
            </a>
          ) : (
            name
          )}
        </div>
        {digest.summary && <div className="text-[10.5px] text-inkfaint truncate">{digest.summary}</div>}
        {travel && travel !== digest.summary && (
          <div className="text-[10.5px] text-inkfaint">{travel}</div>
        )}
      </div>
      <span className="shrink-0 inline-flex items-baseline gap-1">
        <Price value={item.price} source={source} raw={item.price_raw ?? undefined} />
        {unitSuffix && item.price !== null && (
          <span className="text-[9.5px] text-inkfaint">{unitSuffix}</span>
        )}
      </span>
    </li>
  );
}

/** 条目列表（截断到 limit 并注明总数）；空列表如实说明，不编造条目。 */
export function ItemList({
  items,
  icon,
  thumb = false,
  source = "search",
  unitSuffix,
  limit = LIST_LIMIT,
  emptyText = "无条目",
}: {
  items: SearchItem[];
  icon?: ReactNode;
  thumb?: boolean;
  source?: string;
  unitSuffix?: string;
  limit?: number;
  emptyText?: string;
}) {
  if (items.length === 0) {
    return <div className="text-[11.5px] text-inkfaint">{emptyText}</div>;
  }
  const shown = items.slice(0, limit);
  // 未归入摘要的原始字段：默认收起，需要逐条复核时展开（数据不丢失，不占常驻高度）
  const rawLines = shown
    .map((item, i) => ({ name: item.name || `${i + 1}`, more: itemDigest(item).more }))
    .filter((r) => r.more.length > 0);
  return (
    <>
      <ul className="space-y-1.5">
        {shown.map((item, i) => (
          <SearchItemRow
            key={`${item.name}-${i}`}
            item={item}
            icon={icon}
            thumb={thumb}
            source={source}
            unitSuffix={unitSuffix}
          />
        ))}
      </ul>
      {items.length > limit && (
        <div className="text-[11px] text-inkfaint mt-1.5">
          {`仅显示前 ${limit} 条，共 ${items.length} 条`}
        </div>
      )}
      {rawLines.length > 0 && <RawFields rows={rawLines} />}
    </>
  );
}

interface SearchResultCardProps extends ToolCardProps {
  /** 卡头工具语义名 */
  title: string;
  /** 线形图标（设计红线③：禁 emoji） */
  icon: ReactNode;
  /** 无供给时的卡内标题 */
  emptyTitle: string;
  /** 无供给时的可执行下一步（二段式建议的第二段） */
  emptyNext: string;
  /** 价格来源标注（Price source）；搜索原语一律 search */
  source?: string;
  /** 是否显示缩略图位（住宿类有 pic_url） */
  thumb?: boolean;
  /** 单价后缀（住宿「/晚」，设计板 line 585） */
  unitSuffix?: string;
  /** data.summary.hints[hintKey] 命中时优先呈现内核原文建议（如住宿零供给二段式） */
  hintKey?: string;
}

export function SearchResultCard({
  data,
  degraded,
  error,
  title,
  icon,
  emptyTitle,
  emptyNext,
  source = "search",
  thumb = false,
  unitSuffix,
  hintKey,
}: SearchResultCardProps) {
  const rec = asRecord(data);
  const page = toItemPage(rec);
  const hasItems = page.items.length > 0;
  const statusChip = hasItems ? (
    <Chip tone="neutral">{page.total} 条</Chip>
  ) : (
    <Chip tone="ochre">无供给</Chip>
  );
  // 内核若在 payload 内自带建议原文（profile_field_site 的 summary.hints），优先如实转述
  const kernelHint = hintKey ? strOf(asRecord(asRecord(rec.summary).hints)[hintKey]) : "";
  // 失败原因优先于通用空态：hint 是可操作下一步（如改用未来日期），message 是事实描述
  const failureHint = (error?.hint || error?.message || "").trim();

  return (
    <CardShell title={title} icon={icon} degraded={degraded} statusChip={statusChip}>
      {hasItems ? (
        <>
          <ItemList
            items={page.items}
            icon={icon}
            thumb={thumb}
            source={source}
            unitSuffix={unitSuffix}
          />
          <div className="mt-2 pt-2 border-t border-hairline text-[10.5px] text-inkfaint">
            {`最低 ${yuan(page.cheapest)} · 最高 ${yuan(page.dearest)}`}
          </div>
        </>
      ) : (
        <div className="space-y-2.5">
          <EmptyState icon={icon} title={emptyTitle} hint={`返回 ${page.total} 条 · 未取得任何报价`} />
          <Notice tone="ochre">
            {kernelHint ||
              failureHint ||
              `${
                degraded ? "数据源本次未返回有效结果" : "本次查询无可用供给"
              }：该条不计入预算，请人工核验。下一步：${emptyNext}`}
          </Notice>
        </div>
      )}
    </CardShell>
  );
}
