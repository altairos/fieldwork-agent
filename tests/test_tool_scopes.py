# tests/test_tool_scopes.py
"""按阶段裁剪工具 schema（P1-b）：常驻工具 + 阶段专属工具，未知阶段回落全量。"""
from fieldwork_agent.kernel.registry import build_tool_schemas
from fieldwork_agent.runtime.tool_scopes import filter_tool_schemas, phase_tool_names


def test_research_positioning_scope_is_minimal():
    names = phase_tool_names("research_positioning")
    assert {"update_research_context", "get_project_brief",
            "search_literature", "suggest_informants"} <= names
    # 交通工具不属于研究定位阶段
    assert "search_trains" not in names
    assert "search_flights" not in names
    # P2-8：方案生成常驻——用户可在任何阶段明确要求“直接生成方案”
    assert "compose_proposal" in names


def test_field_site_phase_has_geo_tools():
    names = phase_tool_names("field_site_profiles")
    assert {"profile_field_site", "amap_text_search", "amap_around_search"} <= names
    # P2-8：用户常在定位阶段就问住宿，住宿检索不再只留在行程阶段
    assert "search_hotels" in names


def test_itinerary_phase_has_travel_tools():
    names = phase_tool_names("itinerary_planning")
    assert {"search_trains", "search_flights", "compose_itinerary",
            "estimate_living_cost"} <= names
    assert "compose_proposal" in names


def test_proposal_phase_has_doc_tools():
    names = phase_tool_names("proposal_generation")
    assert {"compose_proposal", "compose_interview_guide"} <= names
    assert "search_trains" not in names


def test_unknown_phase_falls_back_to_full_set():
    assert phase_tool_names("no_such_phase") is None
    schemas = build_tool_schemas()
    assert len(filter_tool_schemas(schemas, "no_such_phase")) == len(schemas)


def test_filter_preserves_registry_order():
    schemas = build_tool_schemas()
    all_names = [s["function"]["name"] for s in schemas]
    filtered = filter_tool_schemas(schemas, "research_positioning")
    names = [s["function"]["name"] for s in filtered]
    assert len(names) < len(all_names)
    assert names == [n for n in all_names if n in set(names)]
