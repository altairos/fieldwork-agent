"""Task 1：kernel OpenAI 格式 tools schema → mcp.types.Tool 转译（含 annotations）。"""
from fieldwork_agent.mcp_server.mapping import TOOL_GROUPS, capability_tools
from mcp.types import Tool


def test_capability_tools_are_mcp_tools_with_schema():
    tools = capability_tools()
    assert len(tools) == 21 and all(isinstance(t, Tool) for t in tools)
    flight = next(t for t in tools if t.name == "search_flights")
    assert flight.description
    assert flight.input_schema["type"] == "object"
    assert "origin" in flight.input_schema["properties"]


def test_annotations_groups():
    tools = {t.name: t for t in capability_tools()}
    assert tools["search_hotels"].annotations.read_only_hint is True
    assert tools["search_ai"].annotations.open_world_hint is True
    assert tools["profile_field_site"].annotations.read_only_hint is True
    assert tools["analyze_fieldwork_window"].annotations.read_only_hint is True
    assert tools["plan_last_mile"].annotations.read_only_hint is True
    # amap 六原语：地理查询同属 search 语义（只读+开放世界），不得落入 document 兜底
    assert tools["amap_geocode"].annotations.read_only_hint is True
    assert tools["amap_weather"].annotations.open_world_hint is True
    # 文档组：无外部世界调用（llm 由注入决定），非只读
    assert tools["compose_proposal"].annotations.read_only_hint is False
    assert tools["suggest_informants"].annotations.read_only_hint is False
    # 全等 + 数量钉死：TOOL_GROUPS 键集必须与注册表完全一致
    assert set(TOOL_GROUPS) == {t.name for t in capability_tools()}
    assert len(TOOL_GROUPS) == 21
