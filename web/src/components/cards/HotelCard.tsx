import { IconBed } from "../ui/icons";
import { SearchResultCard } from "./SearchResultCard";
import type { ToolCardProps } from "./types";

/**
 * HotelCard — 住宿搜索结果卡（ItemPage）。
 * 注册给 `search_hotels`（酒店/民宿，驻村调研住宿）与 `search_marriott_hotels`/
 * `search_marriott_packages`（团队接待备选），三者同构 ItemPage（plan doc：search_hotels /
 * search_marriott_* → HotelCard）。
 *
 * 缩略图位 + 每晚价（设计板 lines 583-585：`¥150/晚`，`/晚` 用 text-[9.5px] inkfaint）；
 * 零供给时如实转述内核 `summary.hints.accommodation` 的二段式建议（kernel/aggregation/
 * site_profile.py：枢纽城市住宿为基地 + 当地学术接待，需人工核实），不编造房源。
 */
export function HotelCard(props: ToolCardProps) {
  return (
    <SearchResultCard
      {...props}
      title="住宿搜索结果"
      icon={<IconBed className="w-4 h-4" />}
      thumb
      unitSuffix="/晚"
      hintKey="accommodation"
      emptyTitle="未见住宿供给"
      emptyNext="以最近枢纽城市住宿为基地，进村住宿经当地学术合作方/接待方安排，需人工核实。"
    />
  );
}
