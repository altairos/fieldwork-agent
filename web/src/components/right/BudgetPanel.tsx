import { EmptyState } from "../ui/EmptyState";
import { IconWallet } from "../ui/icons";
import { BudgetCard } from "../cards/registry";
import { asArray, asRecord, strOf } from "../cards/data";
import { useToolsStore, type ToolCall } from "../../stores/tools";

/**
 * BudgetPanel — 右栏「预算」面板（实时状态）。
 *
 * 设计板同源：右栏预算面板 lines 648-659
 *   标题 line 650 `font-serif text-[15px] font-600 mb-3`「预算累计」
 *   合计/上限/进度条/逐条明细 lines 651-658 → **复用 Task 6 `BudgetCard`**（其内部即按
 *   lines 650-658 同源实现：合计 + `/ 上限` + 进度条 + `flex justify-between py-1.5
 *   border-b border-hairsoft` 明细行），面板不重写一套数字视图。
 *   行程元信息行取设计板 line 652 的 `text-[12px] text-inkfaint` 副行语汇。
 *
 * 同源红线②：内核**无独立预算能力**——BudgetBreakdown 随 `compose_itinerary` 的
 * `ItineraryPlan.budget` 一并返回（kernel/aggregation/itinerary.py L330-332），故本面板取
 * tools store 里最新一次 `compose_itinerary` 的 `tool_completed.data.budget`，与中栏
 * ItineraryCard 内嵌的 BudgetBar 是**同一份 payload**；面板 0 处 fetch、0 处金额加工
 * （合计/余额/百分比全在 BudgetCard 内按内核字段计算，价格一律走 `Price`，null → "暂无报价"）。
 */
export function BudgetPanel() {
  const byCallId = useToolsStore((s) => s.byCallId);
  const order = useToolsStore((s) => s.order);

  // 最新一次行程方案（done/failed 都呈现：失败时 BudgetCard 如实说明未取得预算）
  let call: ToolCall | null = null;
  for (const id of order) {
    const c = byCallId[id];
    if (c && c.tool === "compose_itinerary" && c.status !== "running") call = c;
  }

  if (call === null) {
    return (
      <EmptyState
        icon={<IconWallet className="w-4 h-4" />}
        title="尚未生成行程预算"
        hint="预算随行程方案（compose_itinerary）一并生成；先确认田野点与调研天数，罗盘会给出逐条计价口径"
      />
    );
  }

  const plan = asRecord(call.data);
  const budget = plan.budget === undefined ? null : plan.budget;
  const departure = strOf(plan.departure);
  const destinations = asArray(plan.destinations).map((d) => strOf(d)).filter((d) => d !== "");
  const start = strOf(plan.start_date);
  const end = strOf(plan.end_date);
  const generatedAt = strOf(plan.generated_at);
  const route = [departure, ...destinations].filter((s) => s !== "").join(" → ");
  const dateRange = start && end ? `${start} 至 ${end}` : start || end;

  return (
    <div>
      <div className="font-serif text-[15px] font-semibold mb-3">预算累计</div>

      {/* 行程元信息（同一 ItineraryPlan payload 的标识字段，非金额加工） */}
      {(route || dateRange || generatedAt) && (
        <div className="text-[12px] text-inkfaint mb-3">
          {route && <span className="block truncate">{route}</span>}
          {dateRange && <span className="num block">{dateRange}</span>}
          {generatedAt && <span className="num block">{`方案生成于 ${generatedAt}`}</span>}
        </div>
      )}

      <BudgetCard data={budget} degraded={call.degraded ?? null} />
    </div>
  );
}
