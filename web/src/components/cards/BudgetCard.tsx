import { Chip } from "../ui/Chip";
import { Price } from "../ui/Price";
import { PulseDot, type DotTone } from "../ui/PulseDot";
import { IconWallet } from "../ui/icons";
import { CardShell, NoData, Notice } from "./CardShell";
import { SnapshotDisclosure } from "./SnapshotDisclosure";
import { asArray, asRecord, boolOf, numOf, strOf, yuan } from "./data";
import type { ToolCardProps } from "./types";

/**
 * BudgetCard — 预算明细卡（BudgetBreakdown）+ BudgetBar — 预算累计条（分段色条 + 图例）。
 *
 * 内核没有独立的预算能力：`BudgetBreakdown` 随 `compose_itinerary` 的
 * `ItineraryPlan.budget` 一并返回（kernel/aggregation/itinerary.py L330-332），故
 * - `BudgetBar`（设计板 lines 496-509 分段色条 + 图例含来源）由 ItineraryCard 内嵌；
 * - `BudgetCard`（设计板 lines 648-659 右栏预算面板：合计 + 条 + 逐条明细）是同一份
 *   BudgetBreakdown 的完整视图，供 Task 7 右栏「预算」面板复用同源 data（红线②）。
 *
 * 红线③（价格不失真）：金额一律走 `Price`（source → 来源 chip）；null → "暂无报价"，绝不 ¥0。
 * 红线：`coverage !== "complete"` 时**不得**给出"预算内/超支"结论——头部只挂 ochre
 * 「预算不完整」chip，并列出缺报价条目；`over_budget` 仅在内核判定 coverage=complete
 * 时才可能为真（kernel itinerary.py L331），此时才显示 danger 红警。
 */

/** BudgetItem 的容错视图（amount 允许缺失 → null，交给 Price 显示"暂无报价"）。 */
interface BudgetItemView {
  name: string;
  amount: number | null;
  source: string;
  currency: string;
  unit: string;
  quantity: number | null;
  people: number | null;
  priced_at: string;
  source_ref: string;
  amount_low: number | null;
  amount_high: number | null;
  basis: string;
}

interface BudgetView {
  items: BudgetItemView[];
  total: number | null;
  budget_total: number | null;
  over_budget: boolean;
  coverage: string;
  total_high: number | null;
  over_budget_possible: boolean;
  uncovered: string[];
}

/** `unknown` → BudgetView（逐字段容错读，不裸断言）。 */
export function toBudget(data: unknown): BudgetView {
  const rec = asRecord(data);
  const items = asArray(rec.items).map((raw) => {
    const r = asRecord(raw);
    return {
      name: strOf(r.name),
      amount: numOf(r.amount),
      source: strOf(r.source),
      currency: strOf(r.currency),
      unit: strOf(r.unit),
      quantity: numOf(r.quantity),
      people: numOf(r.people),
      priced_at: strOf(r.priced_at),
      source_ref: strOf(r.source_ref),
      amount_low: numOf(r.amount_low),
      amount_high: numOf(r.amount_high),
      basis: strOf(r.basis),
    };
  });
  return {
    items,
    total: numOf(rec.total),
    budget_total: numOf(rec.budget_total),
    over_budget: boolOf(rec.over_budget),
    coverage: strOf(rec.coverage),
    total_high: numOf(rec.total_high),
    over_budget_possible: boolOf(rec.over_budget_possible),
    uncovered: asArray(rec.uncovered).map((u) => strOf(u)).filter((u) => u !== ""),
  };
}

/** coverage → 中文完整度标签（不回显英文枚举，界面单一中文语境）。 */
function coverageLabel(coverage: string): string {
  if (coverage === "complete") return "报价齐备";
  if (coverage === "unknown") return "尚无报价";
  return coverage ? "部分条目缺报价" : "未知";
}

/** 计价口径（单位 × 数量 × 人数 · 报价出处）— 可复核性要求，原样呈现内核字段。 */
function caliber(item: BudgetItemView): string {
  const parts: string[] = [];
  if (item.unit) parts.push(item.unit);
  if (item.quantity !== null) parts.push(`× ${item.quantity}`);
  if (item.people !== null && item.people !== 1) parts.push(`× ${item.people} 人`);
  if (item.source_ref) parts.push(`· ${item.source_ref}`);
  return parts.join(" ");
}

/** 百分比：分母缺失/为 0 时返回 null（不猜、不显示 0%）。 */
function percent(total: number | null, budgetTotal: number | null): number | null {
  if (total === null || budgetTotal === null || budgetTotal <= 0) return null;
  return Math.round((total / budgetTotal) * 100);
}

/**
 * BudgetBar — 预算累计条（设计板 lines 496-509）。
 * 分段色条按条目金额占比着色（line 500-502 indigo/cinnabar/ochre 顺序），
 * 图例含金额与来源（lines 504-508「住宿 ¥13,500 · 90晚×¥150 cheapest」）。
 */
const PALETTE: { bar: string; dot: DotTone }[] = [
  { bar: "bg-indigo", dot: "indigo" },
  { bar: "bg-cinnabar", dot: "cinnabar" },
  { bar: "bg-ochre", dot: "ochre" },
  { bar: "bg-bamboo", dot: "bamboo" },
  { bar: "bg-inkfaint", dot: "neutral" },
];

export function BudgetBar({ budget }: { budget: unknown }) {
  const b = toBudget(budget);
  const denom = b.total !== null && b.total > 0 ? b.total : null;
  const segments = b.items
    .map((item, i) => ({ item, tone: PALETTE[i % PALETTE.length] }))
    .filter((s) => s.item.amount !== null && s.item.amount > 0);

  return (
    <div className="mt-4 pt-4 border-t border-hairline">
      <div className="flex items-baseline justify-between gap-2 mb-2">
        <span className="text-[12.5px] text-inksoft flex items-center gap-1.5">
          <IconWallet className="w-4 h-4 text-indigo" />
          预算累计
        </span>
        <span className="inline-flex items-baseline gap-1.5">
          <Price value={b.total} />
          <span className="num text-[11px] text-inkfaint">{`/ ${yuan(b.budget_total)}`}</span>
        </span>
      </div>
      {denom === null ? (
        <div className="text-[11.5px] text-inkfaint">尚无计价条目，累计条不可用</div>
      ) : (
        <>
          <div className="h-2.5 rounded-full bg-cardsunk overflow-hidden flex">
            {segments.map((s, i) => (
              <div
                key={`${s.item.name}-${i}`}
                className={`h-full ${s.tone.bar}`}
                style={{ width: `${((s.item.amount as number) / denom) * 100}%` }}
                title={`${s.item.name} ${yuan(s.item.amount)}`}
              />
            ))}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[11px] text-inkfaint">
            {segments.map((s, i) => (
              <span key={`${s.item.name}-${i}`} className="flex items-center gap-1 min-w-0">
                <PulseDot tone={s.tone.dot} />
                <span className="truncate">{s.item.name || "未命名条目"}</span>
                <Price value={s.item.amount} source={s.item.source || undefined} />
              </span>
            ))}
          </div>
        </>
      )}
      {b.coverage !== "complete" && (
        <div className="text-[11px] text-ochre mt-2">
          {`尚有 ${b.uncovered.length} 项缺报价，未计入累计；不能据此判断是否在预算内`}
        </div>
      )}
    </div>
  );
}

export function BudgetCard({ data, degraded }: ToolCardProps) {
  const b = toBudget(data);
  const complete = b.coverage === "complete";
  const over = b.over_budget;
  const pct = percent(b.total, b.budget_total);
  const diff =
    b.total !== null && b.budget_total !== null ? Math.abs(b.total - b.budget_total) : null;
  const crossLine = complete && b.over_budget_possible && !over;
  // 结论色：不完整/跨线 → ochre（不给"预算内"结论）；complete 且超支 → danger；否则 bamboo
  const tone = !complete || crossLine ? "ochre" : over ? "danger" : "bamboo";
  const barColor = tone === "danger" ? "bg-danger" : tone === "ochre" ? "bg-ochre" : "bg-bamboo";
  const pctColor =
    tone === "danger" ? "text-danger" : tone === "ochre" ? "text-ochre" : "text-bamboo";

  const statusChip = !complete ? (
    <Chip tone="ochre">预算不完整</Chip>
  ) : over ? (
    <Chip tone="cinnabar">超出预算</Chip>
  ) : crossLine ? (
    <Chip tone="ochre">跨预算线</Chip>
  ) : (
    <Chip tone="bamboo">预算内</Chip>
  );

  return (
    <CardShell
      title="预算明细"
      icon={<IconWallet className="w-4 h-4" />}
      degraded={degraded}
      statusChip={statusChip}
    >
      {data === null || data === undefined ? (
        <NoData hint="未取得预算数据；预算随行程方案（compose_itinerary）一并生成。" />
      ) : (
        <div className="space-y-3">
          {/* 合计 + 上限 + 单条进度（设计板 lines 650-653；超支红警 line 255-256） */}
          <div>
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[12.5px] text-inksoft flex items-center gap-1.5">
                <IconWallet className="w-4 h-4 text-indigo" />
                预算合计
              </span>
              <span className="inline-flex items-baseline gap-1.5">
                <Price value={b.total} />
                <span className="num text-[11px] text-inkfaint">{`/ ${yuan(b.budget_total)}`}</span>
                {pct !== null && <span className={`num text-[11.5px] ${pctColor}`}>{`${pct}%`}</span>}
              </span>
            </div>
            <div className="h-2 rounded-full bg-cardsunk overflow-hidden mt-1.5">
              <div
                className={`h-full rounded-full ${barColor}`}
                style={{ width: `${pct === null ? 0 : Math.min(100, pct)}%` }}
              />
            </div>
            {b.total_high !== null && (
              <div className="text-[11px] text-inkfaint mt-1">
                {`含估算区间，上限约 ${yuan(b.total_high)}`}
              </div>
            )}
          </div>

          {/* 超支红警：只在 coverage=complete（内核此时才置 over_budget）时给结论 */}
          {complete && over && diff !== null && (
            <Notice tone="danger">
              {`合计已高于预算上限 ${yuan(diff)}${pct === null ? "" : `（${pct}%`}${pct === null ? "" : "）"}：需压缩住宿/交通条目或追加经费后重算。`}
            </Notice>
          )}
          {complete && !over && diff !== null && (
            crossLine ? (
              <div className="text-[11.5px] text-ochre">
                {`按下限低于预算 ${yuan(diff)}，按上限（${yuan(b.total_high)}）可能超出，需按区间预留`}
              </div>
            ) : (
              <div className="text-[11.5px] text-bamboo">{`低于预算上限 ${yuan(diff)}（报价齐备，可据此判断）`}</div>
            )
          )}

          {/* 逐条明细（设计板 lines 654-658：`flex justify-between py-1.5 border-b border-hairsoft`） */}
          {b.items.length === 0 ? (
            <div className="text-[11.5px] text-inkfaint">无计价条目</div>
          ) : (
            <ul>
              {b.items.map((item, i) => (
                <li
                  key={`${item.name}-${i}`}
                  className="flex items-start justify-between gap-3 text-[12.5px] py-1.5 border-b border-hairsoft"
                  title={item.priced_at ? `报价时间 ${item.priced_at}` : undefined}
                >
                  <div className="min-w-0 flex-1">
                    <div className="text-inksoft truncate">{item.name || "未命名条目"}</div>
                    {caliber(item) && (
                      <div className="text-[10.5px] text-inkfaint truncate">{caliber(item)}</div>
                    )}
                    {item.basis && (
                      <div className="text-[10.5px] text-inkfaint truncate" title={item.basis}>
                        {item.basis}
                      </div>
                    )}
                    <SnapshotDisclosure sourceRef={item.source_ref} />
                  </div>
                  <span className="shrink-0 inline-flex items-baseline gap-1">
                    {item.amount_low !== null && item.amount_high !== null ? (
                      <span className="num text-[12.5px] text-ink">
                        {`${yuan(item.amount_low)}–${yuan(item.amount_high)}`}
                      </span>
                    ) : (
                      <Price value={item.amount} source={item.source || undefined} />
                    )}
                    {item.currency && item.currency !== "CNY" && (
                      <Chip tone="neutral">{item.currency}</Chip>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}

          {/* 完整度（不回显英文枚举） */}
          <div className="flex items-center gap-2 text-[11.5px] text-inkfaint">
            <span>报价完整度</span>
            <Chip tone={complete ? "bamboo" : "ochre"}>{coverageLabel(b.coverage)}</Chip>
          </div>

          {/* coverage ≠ complete：缺报价条目逐条列出，且不给"预算内"结论 */}
          {!complete && (
            <div className="space-y-2">
              <Notice tone="ochre">
                以下条目未取得报价，未计入合计；据此不能判断是否在预算内，请补齐报价后复核。
              </Notice>
              {b.uncovered.length > 0 && (
                <ul className="flex flex-wrap gap-1.5">
                  {b.uncovered.map((name, i) => (
                    <li key={`${name}-${i}`}>
                      <Chip tone="neutral">{name}</Chip>
                    </li>
                  ))}
                </ul>
              )}
              <div className="text-[10.5px] text-inkfaint">{`缺报价条目 ${b.uncovered.length} 项`}</div>
            </div>
          )}
        </div>
      )}
    </CardShell>
  );
}
