"""dialogue_engine — 工具调用决策、阶段推进、地点抽取、system prompt 选择。"""
from fieldwork_agent.core.state import ProjectState


def _tools(calls):
    return [c["tool"] for c in calls]


# ─── system prompt 按阶段选择 ──────────────────────────

def test_system_prompt_follows_phase(make_engine, project):
    engine = make_engine(project)
    assert "研究定位" in engine.system_prompt
    project.phase = ProjectState.PHASE_ITINERARY_PLANNING
    assert "行程规划" in engine.system_prompt


# ─── 阶段 B：田野点画像，产出 8 个采集调用 ────────────────

def test_decide_phase_b_site_profiles(make_engine, project):
    project.phase = ProjectState.PHASE_FIELD_SITE_PROFILES
    project.set_researcher_info("target_location", "大理")
    engine = make_engine(project)

    calls = engine.decide_tool_calls("帮我看看大理的情况")
    tools = _tools(calls)
    assert tools.count("search_hotel") == 2
    assert tools.count("search_poi") == 4
    assert "ai_search" in tools
    assert "keyword_search" in tools
    assert len(calls) == 8
    # 每个调用都应带目的地信息且 purpose 非空
    for c in calls:
        assert c.get("purpose")


# ─── 阶段 C：行程规划，交通+住宿+文化组合 ──────────────────

def test_decide_phase_c_itinerary(make_engine, project):
    project.phase = ProjectState.PHASE_ITINERARY_PLANNING
    project.set_researcher_info("target_location", "大理")
    project.set_researcher_info("departure_city", "北京")
    project.set_researcher_info("travel_date", "2026-07-01")
    engine = make_engine(project)

    calls = engine.decide_tool_calls("开始规划行程")
    tools = _tools(calls)
    assert "search_flight" in tools
    assert "search_train" in tools
    assert tools.count("search_hotel") == 2
    assert tools.count("search_poi") == 2
    assert "ai_search" in tools
    assert "keyword_search" in tools

    flight = next(c for c in calls if c["tool"] == "search_flight")
    assert flight["args"]["origin"] == "北京"
    assert flight["args"]["destination"] == "大理"
    assert flight["args"]["dep_date"] == "2026-07-01"


def test_decide_phase_c_without_departure_makes_no_calls(make_engine, project):
    project.phase = ProjectState.PHASE_ITINERARY_PLANNING
    # 缺少出发地，行程规划分支不应触发组合调用
    project.set_researcher_info("target_location", "大理")
    engine = make_engine(project)
    calls = engine.decide_tool_calls("规划一下")
    assert calls == []


# ─── 关键词意图（定位阶段的 else 分支）─────────────────────

def test_decide_flight_intent_with_both_cities(make_engine, project):
    project.set_researcher_info("target_location", "大理")
    project.set_researcher_info("departure_city", "北京")
    engine = make_engine(project)

    calls = engine.decide_tool_calls("我想看看机票价格")
    assert "search_flight" in _tools(calls)


def test_decide_hotel_intent_falls_back_to_keyword(make_engine, project):
    # 无目的地时，住宿意图应退化为广义关键词搜索
    engine = make_engine(project)
    calls = engine.decide_tool_calls("有没有推荐的酒店住宿")
    assert "keyword_search" in _tools(calls)
    assert "search_hotel" not in _tools(calls)


def test_decide_ai_intent(make_engine, project):
    engine = make_engine(project)
    calls = engine.decide_tool_calls("给我一些推荐建议")
    assert "ai_search" in _tools(calls)


def test_decide_province_exploration(make_engine, project):
    # 提到省份但未确定具体地点 → 关键词区域探索
    engine = make_engine(project)
    calls = engine.decide_tool_calls("我想到云南做驻村调研")
    assert "keyword_search" in _tools(calls)
    kw = next(c for c in calls if c["tool"] == "keyword_search")
    assert "村落" in kw["args"]["query"]


# ─── 地点抽取 ────────────────────────────────────────

def test_extract_destination(make_engine, project):
    engine = make_engine(project)
    locs = engine._extract_locations_from_message("下周去大理做调研", {})
    assert locs.get("target_location") == "大理"


def test_extract_departure_and_destination(make_engine, project):
    engine = make_engine(project)
    locs = engine._extract_locations_from_message("从北京到昆明的高铁", {})
    assert locs.get("departure_city") == "北京"
    assert locs.get("target_location") == "昆明"


def test_extract_respects_existing_info(make_engine, project):
    engine = make_engine(project)
    existing = {"target_location": "丽江", "departure_city": "上海"}
    locs = engine._extract_locations_from_message("去大理", existing)
    # 已有信息不再覆盖
    assert "target_location" not in locs


# ─── process_user_message：记录 + 提取 + 阶段推进 ───────────

def test_process_advances_positioning_after_four_fields(make_engine, project):
    for key in ["research_discipline", "research_topic", "field_site_status", "scale"]:
        project.set_researcher_info(key, "x")
    engine = make_engine(project)

    result = engine.process_user_message("好的")
    assert project.phase == ProjectState.PHASE_FIELD_SITE_PROFILES
    assert result["msg_meta"].get("phase_transition", {}).get("to") == \
        ProjectState.PHASE_FIELD_SITE_PROFILES
    # 用户消息应被记录进对话上下文
    assert any(m["role"] == "user" for m in project.state["dialogue_context"])


def test_process_does_not_advance_when_incomplete(make_engine, project):
    project.set_researcher_info("research_discipline", "人类学")  # 仅 1 个字段
    engine = make_engine(project)
    engine.process_user_message("随便聊聊")
    assert project.phase == ProjectState.PHASE_RESEARCH_POSITIONING


def test_process_advances_on_confirmed_site(make_engine, project):
    project.phase = ProjectState.PHASE_FIELD_SITE_PROFILES
    project.state["field_sites"] = [{"name": "大理", "confirmed": True}]
    engine = make_engine(project)
    engine.process_user_message("就定大理了")
    assert project.phase == ProjectState.PHASE_ITINERARY_PLANNING


def test_process_returns_llm_payload(make_engine, project):
    engine = make_engine(project)
    result = engine.process_user_message("你好")
    for key in ("system_prompt", "user_message", "phase", "messages_to_llm", "tool_calls"):
        assert key in result
