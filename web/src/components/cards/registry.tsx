import type { ComponentType } from "react";
import { BudgetCard } from "./BudgetCard";
import { FlightCard } from "./FlightCard";
import { HotelCard } from "./HotelCard";
import { InformantCard } from "./InformantCard";
import { ItineraryCard } from "./ItineraryCard";
import { LiteratureCard } from "./LiteratureCard";
import { LivingCostCard } from "./LivingCostCard";
import { PoiCard } from "./PoiCard";
import { ProposalCard } from "./ProposalCard";
import { SiteProfileCard } from "./SiteProfileCard";
import { ToolCardFallback } from "./ToolCardFallback";
import { TrainCard } from "./TrainCard";
import { UpdateResearchContextCard } from "./UpdateResearchContextCard";
import { WindowCard } from "./WindowCard";
import type { ToolCardProps } from "./types";

/**
 * 工具卡片注册表 — `tool name → card component`（红线②：卡片消费 `tool_completed.data`，
 * 是中栏时间线与右栏面板的唯一同源数据）。
 *
 * **键集 = 内核真实能力名（15 项）+ 宿主层状态工具（1 项），逐字对齐，不臆造**：
 * 实测 `fieldwork_agent.kernel.CAPABILITIES()`（kernel/registry.py 单一注册表），
 * kernel registry 面 / `mcp_server/mapping.py` TOOL_GROUPS / `core/orchestrator.py`
 * _CAPABILITY_DISPATCH / 本表 四处一致（8 搜索原语 + 4 聚合 + 3 文档 = 15，
 * 其中 4 聚合含 analyze_fieldwork_window 与 estimate_living_cost）。
 * 轮 2 症状②例外：`update_research_context` 是宿主层状态工具
 * （web_host/agent/state_tools.py 每回合注入 loop，非内核能力），
 * 是唯一超出内核键集的注册项——不注册则状态写入落 Fallback 裸 JSON。
 *
 * 与 brief 示例表的差异（brief line 43 明示"若与上表不符，以内核为准"）：
 * - brief 的 `search_poi/search_domestic_flights/plan_itinerary/compile_budget/get_city_trips/
 *   generate_proposal/generate_document` 在本仓库**不存在**（全库 grep 0 命中），故不作为键；
 * - 内核实名为 `search_pois`/`search_flights`/`compose_itinerary`/`compose_proposal`/
 *   `compose_interview_guide`，另有 `search_keyword`/`search_ai`/`search_marriott_hotels`/
 *   `search_marriott_packages` 四个搜索原语（plan doc lines 143-154 已给出同一映射口径）；
 * - 内核**无独立预算能力**：BudgetBreakdown 随 `compose_itinerary` 的 ItineraryPlan.budget
 *   一并返回（kernel/aggregation/itinerary.py L330-332），故 BudgetCard 不占注册表键——
 *   它以 `BudgetBar` 形式内嵌 ItineraryCard，完整明细视图供 Task 7 右栏「预算」面板复用同源 data；
 * - 同理**无 TripCard**（没有 get_city_trips 这类工具），不建死代码。
 *
 * `ToolCardProps` 定义在 `./types`（打破 registry ↔ 卡片 的循环类型依赖），此处 re-export
 * 以保持 brief 的调用契约：`import { getToolCard, type ToolCardProps } from ".../registry"`。
 */
export type { ToolCardProps };

export const toolCardRegistry: Record<string, ComponentType<ToolCardProps>> = {
  // 聚合能力
  profile_field_site: SiteProfileCard,
  compose_itinerary: ItineraryCard,
  analyze_fieldwork_window: WindowCard,
  estimate_living_cost: LivingCostCard,
  // 文档能力（提案与访谈提纲共用文档卡：payload 分别是 ProposalDoc / {类型: 正文}）
  compose_proposal: ProposalCard,
  compose_interview_guide: ProposalCard,
  suggest_informants: InformantCard,
  // 8 个飞猪搜索原语（同构 ItemPage → 按语义分四类卡）
  search_flights: FlightCard,
  search_trains: TrainCard,
  search_hotels: HotelCard,
  search_marriott_hotels: HotelCard,
  search_marriott_packages: HotelCard,
  search_pois: PoiCard,
  search_keyword: PoiCard,
  search_ai: PoiCard,
  // 学术文献检索（OpenAlex + 中文社科深链）：产出是用户核心交付物，用专属卡原样整卡渲染
  search_literature: LiteratureCard,
  // 宿主层状态工具（轮 2 症状②，见头部注释例外说明）：data = {updated, reason}
  update_research_context: UpdateResearchContextCard,
};

/** 取卡片组件；未注册工具（内核新增能力）落 Fallback，绝不抛错。 */
export function getToolCard(tool: string): ComponentType<ToolCardProps> {
  return toolCardRegistry[tool] ?? ToolCardFallback;
}

/**
 * 工具可见性分级（C 节问题 2，对话流精简）——时间线 settled 区按此分流：
 * - full：聚合/文档产出（用户核心交付物），原样整卡渲染；
 * - collapsed：过程性检索（8 搜索原语），折叠为单行摘要条，点击展开原卡；
 * - hidden：内部状态写入，时间线不显示（成功与失败均不显示，正文自会解释）；
 * 未注册工具（Fallback 卡）默认 full（新能力优先保证可见，再人工分级）。
 * 键集与上方注册表同源（update_research_context 为宿主层状态工具，同样列入）。
 */
export type ToolVisibility = "full" | "collapsed" | "hidden";
export const toolVisibility: Record<string, ToolVisibility> = {
  // full：聚合/文档产出（用户核心交付物）
  profile_field_site: "full",
  compose_itinerary: "full",
  analyze_fieldwork_window: "full",
  estimate_living_cost: "full",
  compose_proposal: "full",
  compose_interview_guide: "full",
  suggest_informants: "full",
  search_literature: "full",
  // collapsed：8 搜索原语（过程性检索）
  search_flights: "collapsed",
  search_trains: "collapsed",
  search_hotels: "collapsed",
  search_marriott_hotels: "collapsed",
  search_marriott_packages: "collapsed",
  search_pois: "collapsed",
  search_keyword: "collapsed",
  search_ai: "collapsed",
  // hidden：内部状态写入
  update_research_context: "hidden",
};

/** Task 7 右栏「预算」面板复用同一份 BudgetBreakdown 视图（同源，不另写一套）。 */
export { BudgetCard };
