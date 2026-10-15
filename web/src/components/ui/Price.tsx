import { Chip } from "./Chip";

/**
 * Price — 价格显示（不失真，设计红线④：所有报价标注来源）。
 * brief 权威实现：value=null/undefined → "暂无报价"（不是 ¥0，不是 ¥null）；
 * 有值 → num（Fraunces + lining-nums，tokens.css line 23）显示 ¥{value}，旁附来源 chip。
 * raw：原始报价文本（SearchItem.price_raw），作 title 供逐条复核。
 * sourceLabel: search→实时搜索 / cheapest→最低报价 / manual→手动录入 / estimate→估算。
 */
function sourceLabel(source: string): string {
  switch (source) {
    case "search":
      return "实时搜索";
    case "cheapest":
      return "最低报价";
    case "manual":
      return "手动录入";
    case "estimate":
      return "估算";
    default:
      return source;
  }
}

export function Price({ value, source, raw }: { value: number | null; source?: string; raw?: string }) {
  if (value === null || value === undefined) {
    return <span className="text-inkfaint text-[12px]">暂无报价</span>;
  }
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="num text-ink" title={raw ?? undefined}>¥{value}</span>
      {source && <Chip tone="neutral">{sourceLabel(source)}</Chip>}
    </span>
  );
}
