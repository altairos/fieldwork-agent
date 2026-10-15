"""Task 1：kernel OpenAI 格式 tools schema → mcp.types.Tool 转译（含 annotations）。"""
from fieldwork_agent.mcp_server.mapping import TOOL_GROUPS, capability_tools
from mcp.types import Tool


def test_capability_tools_are_mcp_tools_with_schema():
    tools = capability_tools()
    assert len(tools) == 13 and all(isinstance(t, Tool) for t in tools)
    flight = next(t for t in tools if t.name == "search_flights")
    assert flight.description
    assert flight.input_schema["type"] == "object"
    assert "origin" in flight.input_schema["properties"]


def test_annotations_groups():
    tools = {t.name: t for t in capability_tools()}
    assert tools["search_hotels"].annotations.read_only_hint is True
    assert tools["search_ai"].annotations.open_world_hint is True
    assert tools["profile_field_site"].annotations.read_only_hint is True
    # 文档组：无外部世界调用（llm 由注入决定），非只读
    assert tools["compose_proposal"].annotations.read_only_hint is False
    assert tools["suggest_informants"].annotations.read_only_hint is False
    # 全等 + 数量钉死：TOOL_GROUPS 键集必须与注册表完全一致
    assert set(TOOL_GROUPS) == {t.name for t in capability_tools()}
    assert len(TOOL_GROUPS) == 13


def test_annotations_follow_tool_groups_for_every_tool():
    """终审①：组标签 → annotations 的映射全量表驱动钉死（抽样 5 个之外，13 个逐一）。

    ``TOOL_GROUPS[t.name]`` 直接下标取值：新工具漏进组标签表会当场 KeyError，
    不会静默落到 ``_annotations`` 的 document 默认分支上蒙混过关。
    """
    tools = capability_tools()
    assert len(tools) == 13
    # 只读：搜索/聚合组 True（只出网读数据），文档组 False（生成内容、会写项目状态）
    assert all(t.annotations.read_only_hint is (TOOL_GROUPS[t.name] != "document")
               for t in tools)
    # 开放世界：搜索/聚合组 True（flyai CLI / 外部检索），文档组 False（llm 由宿主注入）
    assert all(t.annotations.open_world_hint is
               (TOOL_GROUPS[t.name] in ("search", "aggregation")) for t in tools)
