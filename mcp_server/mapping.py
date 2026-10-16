"""kernel OpenAI 格式 tools schema → mcp.types.Tool 转译（spec §2 schema 单一来源的 MCP 侧）。"""
# 注意：kernel/__init__ 把 capabilities 重命名为 CAPABILITIES，这里从 registry 直导原函数
from fieldwork_agent.kernel import build_tool_schemas
from fieldwork_agent.kernel.registry import capabilities
from mcp.types import Tool, ToolAnnotations

# 组标签：决定 annotations；与注册名一一对应，测试钉死
TOOL_GROUPS: dict[str, str] = {
    **{n: "search" for n in ("search_flights", "search_trains", "search_hotels",
                             "search_pois", "search_keyword", "search_ai",
                             "search_marriott_hotels", "search_marriott_packages")},
    "profile_field_site": "aggregation",
    "compose_itinerary": "aggregation",
    "analyze_fieldwork_window": "aggregation",
    "compose_proposal": "document",
    "compose_interview_guide": "document",
    "suggest_informants": "document",
}


def _annotations(name: str) -> ToolAnnotations:
    group = TOOL_GROUPS.get(name, "document")
    if group in ("search", "aggregation"):
        return ToolAnnotations(readOnlyHint=True, openWorldHint=True)
    return ToolAnnotations(readOnlyHint=False, openWorldHint=False)


def capability_tools() -> list[Tool]:
    metas = capabilities()
    tools = []
    for entry in build_tool_schemas():
        fn = entry["function"]
        tools.append(Tool(name=fn["name"], description=fn["description"],
                          inputSchema=fn["parameters"],
                          annotations=_annotations(fn["name"])))
    # 防漂移：注册表与 schema 清单必须全等（单一来源的两面）
    assert {t.name for t in tools} == set(metas), "注册表与 schema 清单漂移"
    return tools
