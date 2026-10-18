import { Chip } from "../ui/Chip";
import { IconWallet } from "../ui/icons";
import { CardShell, NoData } from "./CardShell";
import { asArray, asRecord, numOf, strOf, yuan } from "./data";
import type { ToolCardProps } from "./types";

/**
 * LivingCostCard — 生活成本区间估算卡（`estimate_living_cost`）。
 *
 * 红线（价格不失真）：金额一律来自内核表值区间，卡片只做格式化，不换算、不补 0；
 * 口径（聚落/就餐方式/人数/天数）与数据依据必须与金额同屏，避免被当成当地实时行情。
 */

interface CostItemView {
  name: string;
  per_day_low: number | null;
  per_day_high: number | null;
  amount_low: number | null;
  amount_high: number | null;
  unit: string;
}

export function LivingCostCard({ data, degraded }: ToolCardProps) {
  const rec = asRecord(data);
  const items: CostItemView[] = asArray(rec.items).map((raw) => {
    const r = asRecord(raw);
    return {
      name: strOf(r.name),
      per_day_low: numOf(r.per_day_low),
      per_day_high: numOf(r.per_day_high),
      amount_low: numOf(r.amount_low),
      amount_high: numOf(r.amount_high),
      unit: strOf(r.unit),
    };
  });
  const totalLow = numOf(rec.total_low);
  const totalHigh = numOf(rec.total_high);
  const caliber = strOf(rec.caliber);
  const assumptions = asArray(rec.assumptions)
    .map((a) => strOf(a))
    .filter((a) => a !== "");
  const basis = strOf(rec.basis);
  const coverage = strOf(rec.coverage);

  // 空 data / 无明细：如实说明，不臆造金额（红线③）
  if (items.length === 0) {
    return (
      <CardShell
        title="生活成本估算"
        icon={<IconWallet className="w-4 h-4" />}
        degraded={degraded}
      >
        <NoData hint="未取得生活成本估算：本次调用失败或未返回明细；请检查聚落类型/就餐方式参数后重试。" />
      </CardShell>
    );
  }

  return (
    <CardShell
      title="生活成本估算"
      subtitle={caliber || undefined}
      icon={<IconWallet className="w-4 h-4" />}
      degraded={degraded}
      statusChip={coverage === "estimate"
        ? <Chip tone="ochre">表值估算 · 非实时报价</Chip>
        : undefined}
    >
      <ul>
        {items.map((item, i) => (
          <li
            key={`${item.name}-${i}`}
            className="flex items-start justify-between gap-3 text-[12.5px] py-1.5 border-b border-hairsoft"
          >
            <div className="min-w-0 flex-1">
              <div className="text-inksoft truncate">{item.name || "未命名条目"}</div>
              {item.per_day_low !== null && item.per_day_high !== null && (
                <div className="text-[10.5px] text-inkfaint truncate">
                  {`${yuan(item.per_day_low)}–${yuan(item.per_day_high)} / 人·天 × 人数 × 天数`}
                </div>
              )}
            </div>
            <span className="shrink-0 num text-[12.5px] text-ink">
              {item.amount_low !== null && item.amount_high !== null
                ? `${yuan(item.amount_low)}–${yuan(item.amount_high)}`
                : yuan(item.amount_low ?? item.amount_high)}
            </span>
          </li>
        ))}
      </ul>

      {/* 合计 = 区间（下沿保守、上沿含估算）；null 一律"暂无报价"，绝不补 0 */}
      <div className="mt-2 pt-2 border-t border-hairline text-right num text-[13px] text-ink">
        {totalLow !== null && totalHigh !== null
          ? `合计 ${yuan(totalLow)}–${yuan(totalHigh)}`
          : "合计 暂无报价"}
      </div>

      {assumptions.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[10.5px] text-inkfaint">
          {assumptions.map((a, i) => <li key={`${a}-${i}`}>{`假设：${a}`}</li>)}
        </ul>
      )}
      {basis && (
        <div className="mt-1.5 text-[10.5px] text-inkfaint" title={basis}>
          {`依据：${basis}`}
        </div>
      )}
    </CardShell>
  );
}
