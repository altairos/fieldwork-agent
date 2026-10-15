import { IconPlane } from "../ui/icons";
import { SearchResultCard } from "./SearchResultCard";
import type { ToolCardProps } from "./types";

/**
 * FlightCard — 航班搜索结果卡（`search_flights`，跨省/跨国田野的大交通）。
 *
 * 无供给（items 空）或 degraded（如 flyai_unavailable）时：头部 ochre「降级 · code」chip
 * （设计板 line 447）+ 卡内二段式说明——第一段如实说明"未取得报价、该条不计入预算、
 * 需人工核验"，第二段给可执行下一步（改用 search_trains / 调整日期与临近机场），
 * 与内核 `compose_itinerary` 单腿无价只降级不报错的口径一致（kernel/aggregation/itinerary.py）。
 */
export function FlightCard(props: ToolCardProps) {
  return (
    <SearchResultCard
      {...props}
      title="航班搜索结果"
      icon={<IconPlane className="w-4 h-4" />}
      emptyTitle="未获取到航班班次"
      emptyNext="改用 search_trains 查询同区间火车，或调整出行日期/临近机场后重试。"
    />
  );
}
