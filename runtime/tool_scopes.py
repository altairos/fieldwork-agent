"""按阶段裁剪工具 schema（P1-b）：常驻工具 + 阶段专属工具。

`build_tool_schemas()` 仍是全量单一来源（MCP/CLI 不变）；web 宿主在
`run_turn` 里按当前 phase 过滤，把 32 个工具（23 能力 + 9 状态，约 28.6k
字符）压到 12–22 个，固定开销平均降约 35%。未知 phase 回落全量——阶段字段
异常不能让工具全空。

不裁剪 guidance：收益（约 5k 字符）低于能力损失风险（如行程阶段仍可能
谈访谈安排），本轮只裁工具 schema。
"""
from __future__ import annotations

# 所有阶段常驻：状态读写 + 检索 + 文档产出 + 访谈准备。
# get_project_brief / update_research_context / save_informants 是宿主层
# state tools（不在 registry），列在这里表示“永远不被过滤”的语义锚点。
# P2-8：compose_proposal 常驻——用户在阶段 A/B 明确说“直接帮我生成方案”时，
# 按阶段裁剪会让模型手里没有这个工具，只能反复解释“还没到阶段”。
# 同理，search_flights/search_trains 常驻：用户在定位阶段就问“先查航班/火车”
# 是常见场景（尤其境外田野），没有工具只能让用户等到行程阶段。
# search_pois 是旅行 POI 检索（不是学术/通用搜索），常驻是因为任何阶段都
# 可能需要确认田野点周边设施。
UNIVERSAL_TOOL_NAMES = frozenset({
    "get_project_brief",
    "update_research_context",
    "save_informants",
    "search_literature",
    "search_keyword",
    "search_ai",
    "suggest_informants",
    "compose_interview_guide",
    "compose_proposal",
    "search_pois",
    "search_flights",
    "search_trains",
})

_PHASE_TOOLS: dict[str, frozenset[str]] = {
    # 研究定位：只定问题与方向，不提前规划交通/方案
    "research_positioning": frozenset(),
    # 田野点画像：地理与住宿供给（用户常在定位阶段就问“住哪”，
    # P2-8：search_hotels 放在这里而不是只留到行程阶段）
    "field_site_profiles": frozenset({
        "profile_field_site", "amap_geocode", "amap_regeocode",
        "amap_text_search", "amap_around_search", "amap_weather",
        "estimate_living_cost", "plan_last_mile", "search_hotels",
    }),
    # 行程规划：交通/住宿/路线/时窗/行程生成（交通两件已常驻，此处保留表达
    # “行程阶段工具面”的完整性；并集去重后不重复计入）
    "itinerary_planning": frozenset({
        "search_trains", "search_flights", "search_hotels",
        "search_marriott_hotels", "search_marriott_packages",
        "estimate_living_cost", "plan_last_mile", "amap_direction_transit",
        "amap_weather", "compose_itinerary", "analyze_fieldwork_window",
        "profile_field_site",
    }),
    # 方案生成：文档产出 + 必要时补画像/行程
    "proposal_generation": frozenset({
        "profile_field_site", "compose_itinerary",
        "analyze_fieldwork_window",
    }),
    # 完成后修改：文档重写与补充
    "finished": frozenset({
        "compose_itinerary", "profile_field_site",
    }),
}


def phase_tool_names(phase: str) -> set[str] | None:
    """该阶段可用工具名集合；未知阶段返回 ``None``（调用方回落全量）。"""
    extra = _PHASE_TOOLS.get(phase)
    if extra is None:
        return None
    return set(UNIVERSAL_TOOL_NAMES) | set(extra)


def filter_tool_schemas(schemas: list[dict], phase: str) -> list[dict]:
    """按阶段过滤 registry schema；保持原顺序，未知阶段原样返回。"""
    allowed = phase_tool_names(phase)
    if allowed is None:
        return schemas
    return [s for s in schemas
            if (s.get("function") or {}).get("name") in allowed]
