import { IconTemple } from "../ui/icons";
import { SearchResultCard } from "./SearchResultCard";
import type { ToolCardProps } from "./types";

/**
 * PoiCard — 兴趣点/关键词检索结果卡（ItemPage）。
 * 注册给 `search_pois`（文化场所、节庆、宗教/古迹）、`search_keyword`（签证/租车/保险/美食
 * 广义覆盖）——两者返回同构 ItemPage，共用轻量列表（plan doc「工具→卡片渲染器注册表」：
 * search_pois → POICard、search_keyword → 轻量列表）。`search_ai` 自 Task 4（spec A3/A4）
 * 起改绑 AiSearchCard（answer Markdown 直答），不再走本卡。
 * 条目名带 jump_url 时作靛蓝链接（设计板 line 180 indigo = 数据/链接）。
 */
export function PoiCard(props: ToolCardProps) {
  return (
    <SearchResultCard
      {...props}
      title="检索结果 · 兴趣点"
      icon={<IconTemple className="w-4 h-4" />}
      emptyTitle="未检索到兴趣点"
      emptyNext="改用 search_keyword 关键词检索或 search_ai 自然语言检索重试，或放宽分类/关键词。"
    />
  );
}
