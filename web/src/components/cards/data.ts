import type { ItemPage, SearchItem } from "../../types/models";

/**
 * 卡片层数据容错工具 — `tool_completed.data` 是 `unknown`（models.ts 只是契约镜像，
 * 线上可能缺字段/为 null/类型漂移），故所有卡片一律经这里的读函数取值，
 * 绝不裸 `as` 后直接点属性（否则一个缺字段就白屏）。
 *
 * 价格不失真红线：读数字只区分「有值 / 无值」，无值一律 null（由 `Price` 渲染
 * "暂无报价"），**不做 0 兜底、不做换算**；`yuan()` 仅用于复合文本行
 * （如「最低 ¥180 · 最高 ¥180」摘要、图例），同样 null → "暂无报价"。
 */

/** 对象读法：非对象（null/数组/标量）一律视为空对象。 */
export function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** 数组读法：非数组一律视为空数组。 */
export function asArray<T = Record<string, unknown>>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

/** 有限数字读法：缺失/非数字/NaN/Infinity → null（不用 0 兜底，防价格失真）。 */
export function numOf(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** 字符串读法：非字符串 → ""。 */
export function strOf(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** 布尔读法：仅 true 为真。 */
export function boolOf(value: unknown): boolean {
  return value === true;
}

/** 金额 → 文本；null/undefined 一律 "暂无报价"（绝不 ¥0）。 */
export function yuan(value: number | null | undefined): string {
  return value === null || value === undefined ? "暂无报价" : `¥${value}`;
}

/**
 * `unknown` → ItemPage（容错）。
 * total/cheapest/dearest **显式为 null 时保留 null**（内核明确"无报价"，不得改成 0）；
 * 仅在字段整体缺失（undefined）时由 items 的实价回推极值，避免旧 payload 少字段就失真。
 */
export function toItemPage(data: unknown): ItemPage {
  const rec = asRecord(data);
  const items: SearchItem[] = asArray(rec.items).map((raw) => {
    const r = asRecord(raw);
    return {
      name: strOf(r.name),
      price: numOf(r.price),
      price_raw: r.price_raw == null ? null : strOf(r.price_raw),
      jump_url: r.jump_url == null ? null : strOf(r.jump_url),
      pic_url: r.pic_url == null ? null : strOf(r.pic_url),
      extra: asRecord(r.extra),
    };
  });
  const priced = items.map((i) => i.price).filter((p): p is number => p !== null);
  const cheapest = rec.cheapest === undefined
    ? (priced.length ? Math.min(...priced) : null)
    : numOf(rec.cheapest);
  const dearest = rec.dearest === undefined
    ? (priced.length ? Math.max(...priced) : null)
    : numOf(rec.dearest);
  return { items, total: numOf(rec.total) ?? items.length, cheapest, dearest };
}

/**
 * SearchItem.extra 摘要：取前 3 个标量键值（时长/换乘/星级等来源信息），
 * 原样呈现不加工；非标量值跳过。设计板 line 568「直飞 55min · search」同款副行。
 */
export function extraLine(extra: Record<string, unknown>, limit = 3): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(extra)) {
    if (parts.length >= limit) break;
    if (typeof value === "string" && value.trim()) parts.push(`${key} ${value}`);
    else if (typeof value === "number" && Number.isFinite(value)) parts.push(`${key} ${value}`);
  }
  return parts.join(" · ");
}

/** 列表截断上限：卡片是时间线内的摘要，长列表只显前 N 条并注明总数。 */
export const LIST_LIMIT = 5;
