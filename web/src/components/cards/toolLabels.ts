/**
 * 工具中文名共享表 — `tool name → 中文显示名`（C 节问题 3：对话流不露英文工具名）。
 *
 * 单一来源：`CollapsedToolCard`（折叠摘要条标题）与 `ToolActivityStrip`（活动条行）
 * 共用此表，消除两处各维护一份的漂移风险。键集与内核真实能力名 + 宿主层状态工具
 * 逐字对齐（同 `registry.tsx` 的 toolCardRegistry/toolVisibility 口径）：
 * 8 飞猪搜索原语 + 6 高德地理原语 + 5 聚合能力 + 3 文档能力 + 2 宿主状态工具
 * + 文献检索（search_literature）。未命中的新能力回落原始工具名（不臆造、不抛错）。
 */
export const TOOL_LABELS: Record<string, string> = {
  // 8 飞猪搜索原语
  search_flights: "航班搜索",
  search_trains: "火车票搜索",
  search_hotels: "酒店搜索",
  search_marriott_hotels: "万豪酒店搜索",
  search_marriott_packages: "万豪套餐搜索",
  search_pois: "兴趣点搜索",
  search_keyword: "关键词检索",
  search_ai: "AI 检索",
  // 学术文献检索（OpenAlex + 国家哲社文献中心）
  search_literature: "文献检索",
  // 6 高德地理原语
  amap_geocode: "地理编码",
  amap_regeocode: "逆地理编码",
  amap_text_search: "地点检索",
  amap_around_search: "周边检索",
  amap_direction_transit: "公交接驳",
  amap_weather: "天气查询",
  // 5 聚合能力
  profile_field_site: "田野点画像",
  compose_itinerary: "行程规划",
  analyze_fieldwork_window: "时间窗分析",
  plan_last_mile: "末端接驳",
  estimate_living_cost: "生活成本估算",
  // 3 文档能力
  compose_proposal: "研究方案草稿生成",
  compose_interview_guide: "访谈提纲生成",
  suggest_informants: "访谈对象候选生成",
  // 2 宿主层状态工具
  update_research_context: "研究信息写入",
  get_project_brief: "项目摘要查询",
};

/** 取工具中文名；未注册的新能力回落原始工具名（绝不抛错）。 */
export function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? tool;
}
