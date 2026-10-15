import type { ReactNode } from "react";
import { Chip } from "../ui/Chip";
import { Price } from "../ui/Price";
import { IconBed, IconCaret, IconCompass, IconPlane, IconTrain } from "../ui/icons";
import { BudgetBar, toBudget } from "./BudgetCard";
import { CardShell, NoData } from "./CardShell";
import { asArray, asRecord, boolOf, extraLine, numOf, strOf } from "./data";
import type { ToolCardProps } from "./types";

/**
 * ItineraryCard — 行程方案卡（`compose_itinerary` → ItineraryPlan）。
 *
 * 设计板同源：中栏 ItineraryTimeline lines 465-511
 *   - 折叠外壳 lines 465-471 → `CardShell`（头部状态 chip 取 line 470「预算内」bamboo）
 *   - 竖向时间线 lines 474-491：`ol.relative border-l border-hairline ml-2 space-y-4`
 *     + `li.pl-5 relative` + 圆点 `absolute -left-[5px] top-1 w-2.5 h-2.5 rounded-full`
 *     （段色循环 cinnabar/indigo/bamboo，line 475/479/483；缓冲日 ochre，line 487）
 *     + 段名 `b.text-[13.5px]` + Day 区间 `num text-[11.5px] text-inkfaint`（line 476 `Day 1–9`）
 *     + 描述 `text-[12.5px] text-inksoft mt-0.5`（line 477）
 *   - 备选路线折叠 lines 492-495：`details.mt-3 hairline rounded-md bg-cardsunk/50`
 *     + summary `px-3 py-2 text-[12.5px] text-inksoft` + caret
 *   - BudgetBar lines 496-509 → 复用 `BudgetCard.tsx` 导出的 `BudgetBar`（同一份
 *     ItineraryPlan.budget，右栏预算面板 Task 7 复用同源 data，红线②）
 *   - legs/stays 条目行取右栏交通/住宿行样式 lines 566-575 / 583-585
 *
 * 红线③④：交通/住宿金额一律走 `Price`（内核 `_select` 取最低价 → source="cheapest"；
 * 未选候选来自实时搜索 → source="search"）；`nightly_price`/`chosen.price` 缺失 → "暂无报价"，
 * 绝不 ¥0；`long_stay_unverified` 如实挂 ochre「长住日价待核验」chip（itinerary.py L284-287）。
 * 红线：`coverage !== complete` 时头部只挂「预算不完整」，**不给**"预算内/超支"结论。
 */

/** 段色循环（设计板 lines 475/479/483）。 */
const DOT_CYCLE = ["bg-cinnabar", "bg-indigo", "bg-bamboo"];

/** direction → 中文（itinerary.py L215-218：outbound/inter/return）。 */
const DIRECTION: Record<string, string> = {
  outbound: "去程",
  inter: "站间",
  return: "返程",
};

/** mode → 线形图标（itinerary.py L223-235：flight/train；住宿 民宿/酒店）。 */
function modeIcon(mode: string): ReactNode {
  if (mode === "flight") return <IconPlane className="w-4 h-4" />;
  if (mode === "train") return <IconTrain className="w-4 h-4" />;
  if (mode === "民宿" || mode === "酒店") return <IconBed className="w-4 h-4" />;
  return <IconCompass className="w-4 h-4" />;
}

/** 日历天数（末日为退房/返程日 → +1）；日期不可解析时返回 null（不猜天数）。 */
function spanDays(start: string, end: string): number | null {
  const s = Date.parse(start);
  const e = Date.parse(end);
  if (Number.isNaN(s) || Number.isNaN(e) || e < s) return null;
  return Math.round((e - s) / 86400000) + 1;
}

/**
 * 条目行（设计板 lines 566-575：图标位 + 主行/副行 + 右侧价格）。
 * 渲染为 `<li>`，note（如无可用报价的内核原文）挂在中间列内，避免 li 套 li。
 */
function Row({
  icon,
  main,
  sub,
  price,
  tail,
  note,
}: {
  icon: ReactNode;
  main: string;
  sub: string;
  price: ReactNode;
  tail?: ReactNode;
  note?: string;
}) {
  return (
    <li className="flex items-center gap-2.5">
      <span className="w-8 h-8 rounded-md bg-cardsunk grid place-items-center shrink-0 text-inkfaint">
        {icon}
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-[12.5px] text-ink truncate">{main}</div>
        {sub && <div className="text-[10.5px] text-inkfaint truncate">{sub}</div>}
        {note && <div className="text-[10.5px] text-ochre mt-0.5">{note}</div>}
      </div>
      {tail}
      <span className="shrink-0 inline-flex items-baseline gap-1">{price}</span>
    </li>
  );
}

/** 分节小标题（设计板 line 444 的 tracking 小标）。 */
function SubTitle({ children }: { children: ReactNode }) {
  return <div className="text-[10.5px] tracking-[.16em] text-inkfaint mt-4 mb-2">{children}</div>;
}

export function ItineraryCard({ data, degraded }: ToolCardProps) {
  const rec = asRecord(data);
  const hasPlan = data !== null && data !== undefined;
  const departure = strOf(rec.departure);
  const destinations = asArray(rec.destinations).map((d) => strOf(d)).filter((d) => d !== "");
  const startDate = strOf(rec.start_date);
  const endDate = strOf(rec.end_date);
  const legs = asArray(rec.legs);
  const stays = asArray(rec.stays);
  const rhythm = asArray(rec.rhythm);
  const bufferDays = numOf(rec.buffer_days);
  const alternatives = asArray(rec.alternatives);
  const budget = toBudget(rec.budget);
  const generatedAt = strOf(rec.generated_at);
  const inputsHash = strOf(rec.inputs_hash);

  const days = spanDays(startDate, endDate);
  const route = [departure, ...destinations].filter((s) => s !== "").join(" → ");
  const subtitle = [route, days === null ? "" : `${days} 天`].filter((s) => s !== "").join(" · ");

  // 结论 chip：不完整 → ochre（不给结论）；complete 且超支 → cinnabar；否则 bamboo（line 470）
  const complete = budget.coverage === "complete";
  const statusChip = !hasPlan ? undefined : !complete ? (
    <Chip tone="ochre">预算不完整</Chip>
  ) : budget.over_budget ? (
    <Chip tone="cinnabar">超出预算</Chip>
  ) : (
    <Chip tone="bamboo">预算内</Chip>
  );

  // rhythm 累计 → Day 区间（设计板 line 476/480/484 的 Day 1–9 / 10–54 / 55–78）
  let cursor = 0;
  const phases = rhythm.map((raw, i) => {
    const seg = asRecord(raw);
    const segDays = numOf(seg.days) ?? 0;
    const from = cursor + 1;
    cursor += segDays;
    return {
      label: strOf(seg.label) || `第 ${i + 1} 段`,
      note: strOf(seg.note),
      range: segDays > 0 ? `Day ${from}–${cursor}` : "",
      dot: DOT_CYCLE[i % DOT_CYCLE.length],
    };
  });

  return (
    <CardShell
      title="行程方案"
      subtitle={subtitle || undefined}
      icon={<IconCompass className="w-4 h-4" />}
      degraded={degraded}
      statusChip={statusChip}
    >
      {!hasPlan ? (
        <NoData hint="未取得行程方案；可重试 compose_itinerary，或先确认田野点与时间窗口。" />
      ) : (
        <>
          {/* 竖向时间线（设计板 lines 474-491） */}
          {phases.length === 0 && bufferDays === null ? (
            <div className="text-[11.5px] text-inkfaint">未取得节奏分段</div>
          ) : (
            <ol className="relative border-l border-hairline ml-2 space-y-4">
              {phases.map((p, i) => (
                <li key={`${p.label}-${i}`} className="pl-5 relative">
                  <span className={`absolute -left-[5px] top-1 w-2.5 h-2.5 rounded-full ${p.dot}`} />
                  <div className="flex items-baseline gap-2">
                    <b className="text-[13.5px]">{p.label}</b>
                    {p.range && <span className="num text-[11.5px] text-inkfaint">{p.range}</span>}
                  </div>
                  {p.note && <div className="text-[12.5px] text-inksoft mt-0.5">{p.note}</div>}
                </li>
              ))}
              {bufferDays !== null && bufferDays > 0 && (
                <li className="pl-5 relative">
                  <span className="absolute -left-[5px] top-1 w-2.5 h-2.5 rounded-full bg-ochre" />
                  <div className="flex items-baseline gap-2">
                    <b className="text-[13.5px] text-ochre">缓冲日</b>
                    <span className="num text-[11.5px] text-inkfaint">{`${bufferDays} 天机动`}</span>
                  </div>
                  <div className="text-[12.5px] text-inksoft mt-0.5">
                    分散嵌入各段，应对交通中断与访谈对象改期。
                  </div>
                </li>
              )}
            </ol>
          )}

          {/* 交通 legs（chosen = 内核所选最低价；无价则如实转述内核 note 原文） */}
          {legs.length > 0 && (
            <>
              <SubTitle>交通</SubTitle>
              <ul className="space-y-2">
                {legs.map((raw, i) => {
                  const leg = asRecord(raw);
                  const hasChosen = leg.chosen !== null && leg.chosen !== undefined;
                  const chosen = asRecord(leg.chosen);
                  const sub = [
                    DIRECTION[strOf(leg.direction)] ?? strOf(leg.direction),
                    strOf(leg.date),
                    hasChosen ? strOf(chosen.name) : "",
                    hasChosen ? extraLine(asRecord(chosen.extra), 2) : "",
                  ]
                    .filter((s) => s !== "")
                    .join(" · ");
                  return (
                    <Row
                      key={`leg-${i}`}
                      icon={modeIcon(strOf(chosen.mode))}
                      main={`${strOf(leg.from)} → ${strOf(leg.to)}`}
                      sub={sub}
                      note={strOf(leg.note) || undefined}
                      price={
                        <Price
                          value={hasChosen ? numOf(chosen.price) : null}
                          source={hasChosen ? "cheapest" : undefined}
                          raw={hasChosen ? strOf(chosen.price_raw) || undefined : undefined}
                        />
                      }
                    />
                  );
                })}
              </ul>
            </>
          )}

          {/* 住宿 stays（每晚价 = nightly_price；长住估算待核验如实标注） */}
          {stays.length > 0 && (
            <>
              <SubTitle>住宿</SubTitle>
              <ul className="space-y-2">
                {stays.map((raw, i) => {
                  const stay = asRecord(raw);
                  const hasChosen = stay.chosen !== null && stay.chosen !== undefined;
                  const chosen = asRecord(stay.chosen);
                  const nights = numOf(stay.nights);
                  const nightly = numOf(stay.nightly_price);
                  const sub = [
                    strOf(stay.dest),
                    `${strOf(stay.check_in)} → ${strOf(stay.check_out)}`,
                    nights === null ? "" : `${nights} 晚`,
                    hasChosen ? strOf(chosen.mode) : "",
                  ]
                    .filter((s) => s !== "")
                    .join(" · ");
                  return (
                    <Row
                      key={`stay-${i}`}
                      icon={<IconBed className="w-4 h-4" />}
                      main={(hasChosen && strOf(chosen.name)) || strOf(stay.dest) || "住宿"}
                      sub={sub}
                      tail={boolOf(stay.long_stay_unverified) ? <Chip tone="ochre">长住日价待核验</Chip> : undefined}
                      price={
                        <>
                          <Price value={nightly} source={hasChosen ? "cheapest" : undefined} />
                          {nightly !== null && <span className="text-[9.5px] text-inkfaint">/晚</span>}
                        </>
                      }
                    />
                  );
                })}
              </ul>
            </>
          )}

          {/* 备选路线折叠（设计板 lines 492-495）：未选候选如实列出，不与所选混淆 */}
          {alternatives.length > 0 && (
            <details className="mt-3 hairline rounded-md bg-cardsunk/50">
              <summary className="px-3 py-2 text-[12.5px] text-inksoft flex items-center gap-1.5">
                <IconCaret className="w-3.5 h-3.5 caret" />
                {`备选路线（${alternatives.length}）`}
              </summary>
              <div className="px-3 pb-3 space-y-2.5">
                {alternatives.map((raw, i) => {
                  const group = asRecord(raw);
                  const items = asArray(group.items);
                  return (
                    <div key={`alt-${i}`}>
                      <div className="text-[11px] text-inkfaint mb-1">
                        {`${strOf(group.from)} → ${strOf(group.to)}`}
                      </div>
                      <ul className="space-y-1.5">
                        {items.map((cand, j) => {
                          const c = asRecord(cand);
                          const mode = strOf(c.mode);
                          return (
                            <li key={`alt-${i}-${j}`} className="flex items-center gap-2 text-[12px]">
                              <span className="w-6 h-6 rounded-md bg-card grid place-items-center shrink-0 text-inkfaint">
                                {modeIcon(mode)}
                              </span>
                              <span className="flex-1 min-w-0 truncate text-inksoft">
                                {strOf(c.name) || "未命名候选"}
                              </span>
                              {mode && <Chip tone="neutral">{mode}</Chip>}
                              <Price value={numOf(c.price)} source="search" raw={strOf(c.price_raw) || undefined} />
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  );
                })}
              </div>
            </details>
          )}

          {/* BudgetBar（设计板 lines 496-509）：与右栏预算面板同源同组件 */}
          <BudgetBar budget={rec.budget} />

          {(generatedAt || inputsHash) && (
            <div className="mt-3 flex items-center gap-2 text-[10.5px] text-inkfaint">
              {generatedAt && <span className="num">{generatedAt}</span>}
              {inputsHash && <span className="num ml-auto">inputs {inputsHash}</span>}
            </div>
          )}
        </>
      )}
    </CardShell>
  );
}
