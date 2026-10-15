import { IconTrain } from "../ui/icons";
import { SearchResultCard } from "./SearchResultCard";
import type { ToolCardProps } from "./types";

/**
 * TrainCard — 火车/高铁搜索结果卡（`search_trains`，中小城市与短途田野点大交通）。
 * 与 FlightCard 同一降级口径：无供给/degraded 时头部 ochre chip + 二段式说明
 * （设计板 line 447「降级 · flyai_unavailable」+ line 573「search_trains 降级」灰显语义）。
 */
export function TrainCard(props: ToolCardProps) {
  return (
    <SearchResultCard
      {...props}
      title="火车搜索结果"
      icon={<IconTrain className="w-4 h-4" />}
      emptyTitle="未获取到列车班次"
      emptyNext="改用 search_flights 查询同区间航班，或调整出行日期/临近车站后重试。"
    />
  );
}
