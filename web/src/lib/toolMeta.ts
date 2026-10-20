/**
 * 工具元数据（纯数据，零依赖）— `tool name → 中文名 / 可见性分级`。
 *
 * 为什么要单独一个文件（评审 P1-3）：这两张表原先住在 `components/cards/registry.tsx`，
 * 而那个模块顶部 import 了 17 个卡片组件（含 react-markdown 整条链路）。于是
 * `stores/ui.ts` 为了查一个字符串，把整棵卡片树拉进了状态层的依赖图——单测导入 ui store
 * 就连带加载全部卡片组件，打包也难以按层拆分。
 *
 * 依赖方向从此固定为 `components/* / stores/* / lib/* → lib/toolMeta`，本文件不 import 任何模块。
 * registry 只剩 `tool → 卡片组件` 一件事（它天然依赖组件层，那是它的职责）。
 */

/** 工具可见性分级（C 节问题 2，对话流精简）：
 * - full：聚合/文档产出（用户核心交付物），原样整卡渲染；
 * - collapsed：过程性检索（搜索原语 + 文献/地点检索，spec C3），折叠为单行摘要条，
 *   并进入中栏「检索结果」tab 的证据层；
 * - hidden：内部状态读写与内部地理管线，时间线不显示（成功与失败均不显示，正文自会解释）。
 * 未登记工具（Fallback 卡）默认 full——新能力优先保证可见，再人工分级。 */
export type ToolVisibility = "full" | "collapsed" | "hidden";

export const TOOL_VISIBILITY: Record<string, ToolVisibility> = {
  // full：聚合/文档产出（用户核心交付物）
  profile_field_site: "full",
  plan_last_mile: "full",
  compose_itinerary: "full",
  analyze_fieldwork_window: "full",
  estimate_living_cost: "full",
  compose_proposal: "full",
  compose_interview_guide: "full",
  suggest_informants: "full",
  // collapsed：过程性检索（8 搜索原语 + 文献检索 + 地点检索，spec C3）
  search_flights: "collapsed",
  search_trains: "collapsed",
  search_hotels: "collapsed",
  search_marriott_hotels: "collapsed",
  search_marriott_packages: "collapsed",
  search_pois: "collapsed",
  search_keyword: "collapsed",
  search_ai: "collapsed",
  search_literature: "collapsed",  // 文献检索改入证据 tab（spec C3 delta；精细过滤延后，见备忘）
  amap_text_search: "collapsed",
  // hidden：内部状态读写（每回合由 build_state_tools 注入，非用户交付物，正文自会解释）
  update_research_context: "hidden",
  get_project_brief: "hidden",
  // 内部地理管线（profile_field_site/行程内部调用，非用户交付物，spec C3）：
  // 快照以 amap_ 前缀落盘，hidden 后不进证据 tab（amap_text_search 除外）
  amap_geocode: "hidden",
  amap_regeocode: "hidden",
  amap_around_search: "hidden",
  amap_direction_transit: "hidden",
  amap_weather: "hidden",
};

/** 工具中文名 — `tool name → 中文显示名`（C 节问题 3：对话流不露英文工具名）。
 * 消费方：折叠摘要条、活动条、证据 tab。键集与内核真实能力名 + 宿主层状态工具对齐；
 * 未命中的新能力回落原始工具名（不臆造、不抛错）。 */
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

/** 取工具中文名；未登记的新能力回落原始工具名（绝不抛错）。 */
export function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? tool;
}

/** 取可见性；未登记默认 full（新能力先可见，再人工分级）。
 * 这张表是唯一判定入口——四处各写一遍 `(toolVisibility[t] ?? "full")` 即是评审 P1-3 指出的散落。 */
export function visibilityOf(tool: string): ToolVisibility {
  return TOOL_VISIBILITY[tool] ?? "full";
}

/** 是否应当在时间线/活动条/证据 tab 中完全不出现（hidden 的成功与失败均不显示，spec C3）。 */
export function isHiddenTool(tool: string): boolean {
  return visibilityOf(tool) === "hidden";
}

/**
 * 中栏「对话」tab 内联渲染的例外（用户反馈，2026-10-20）：
 * 田野点画像由右栏「田野点」面板整块承担（快照 + 本回合 live 画像同源），
 * 中栏再渲染一遍是同一份 payload 的重复投影，只会把对话流顶得更长。
 *
 * 只影响**时间线内联**：可见性仍是 full（不触发检索结果红点、证据 tab 准入不变），
 * 注册表映射也不变——将来若要恢复内联，删掉这里的键即可。
 */
export const TIMELINE_OMIT: ReadonlySet<string> = new Set(["profile_field_site"]);

/** 时间线是否内联该工具的结果卡（full 且不在 TIMELINE_OMIT 中）。 */
export function showsInlineInTimeline(tool: string): boolean {
  return visibilityOf(tool) === "full" && !TIMELINE_OMIT.has(tool);
}
