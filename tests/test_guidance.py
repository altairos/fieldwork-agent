"""guidance 子包测试 — 阶段推进纯函数、消息提取、访谈对象库（全部纯数据，不联网不调 LLM）。"""

from fieldwork_agent.kernel.guidance import extract, informants, phases
from fieldwork_agent.kernel.models import FieldSiteRecord, InformantRecord, ResearchContext


def test_check_advance_a_to_b_needs_4_fields():
    ctx = ResearchContext(discipline="人类学", topic="宗族", scale="单人", budget="2万")
    r = phases.check_advance("research_positioning", ctx, [], [], False, False)
    assert r.can_advance and r.to_phase == "field_site_profiles"
    r2 = phases.check_advance(
        "research_positioning", ResearchContext(discipline="人类学", topic="x"), [], [], False, False
    )
    assert not r2.can_advance and set(r2.missing_fields) & {"scale", "budget"}


def test_check_advance_b_to_c_requires_site_and_informant():
    site = FieldSiteRecord(name="喜洲", confirmed=True)
    ctx = ResearchContext(target_location="喜洲")
    no_informant = phases.check_advance("field_site_profiles", ctx, [site], [], False, False)
    assert not no_informant.can_advance, "B→C 缺访谈对象必须不推进(spec §2.1)"
    ok = phases.check_advance(
        "field_site_profiles", ctx, [site], [InformantRecord(code="P1", type="长老")], False, False
    )
    assert ok.can_advance and ok.to_phase == "itinerary_planning"


def test_extract_locations_from_message():
    loc = extract.extract_locations("我从北京出发去大理调研白族村落", {})
    assert loc == {"target_location": "大理", "departure_city": "北京"}


def test_informant_library_shape():
    assert len(informants.INFORMANT_LIBRARY) >= 10
    one = informants.get_informant("长老") or next(iter(informants.INFORMANT_LIBRARY.values()))
    assert one["approach"] and one["question_domains"] and one["ethics"]


def test_prompts_have_no_placeholders():
    for p in phases.PHASES:
        assert "{" not in phases.phase_prompt(p).replace("{{", "").replace("}}", "")


# ---------------------------------------------------------------- 补充覆盖


def test_phases_constants_and_prompts_cover_all_phases():
    assert phases.PHASES == [
        "research_positioning",
        "field_site_profiles",
        "itinerary_planning",
        "proposal_generation",
        "finished",
    ]
    for p in phases.PHASES:
        assert phases.phase_prompt(p), f"{p} 缺 system prompt"
        assert p in phases.REQUIRED_FIELDS
    # 迁移原文：A 阶段提示词必须点出六个定位维度
    assert "学科取向" in phases.phase_prompt("research_positioning")
    assert "时间窗口" in phases.phase_prompt("research_positioning")


def test_unknown_phase_falls_back_to_positioning():
    assert phases.phase_prompt("nope") == phases.phase_prompt("research_positioning")
    assert phases.guiding_questions("nope") == phases.guiding_questions("research_positioning")
    r = phases.check_advance("nope", ResearchContext(), [], [], False, False)
    assert not r.can_advance and r.to_phase is None


def test_guiding_questions_are_non_empty_strings():
    for p in phases.PHASES:
        qs = phases.guiding_questions(p)
        assert isinstance(qs, list)
        assert all(isinstance(q, str) and q for q in qs)
    assert len(phases.guiding_questions("research_positioning")) >= 4


def test_check_advance_required_fields_shape():
    assert phases.REQUIRED_FIELDS["research_positioning"] == [
        "discipline",
        "topic",
        "scale",
        "budget",
        "time_window",
    ]
    assert "target_location" in phases.REQUIRED_FIELDS["field_site_profiles"]
    assert phases.REQUIRED_FIELDS["finished"] == []


def test_check_advance_extras_can_fill_positioning_field():
    """字段本体为空但 extras 同名有值时同样计数（与旧引擎 info 字典语义一致）。"""
    ctx = ResearchContext(
        discipline="社会学", topic="乡绅", scale="单人", extras={"budget": "自费"}
    )
    r = phases.check_advance("research_positioning", ctx, [], [], False, False)
    assert r.can_advance and r.to_phase == "field_site_profiles"


def test_check_advance_b_to_c_missing_keys():
    """缺 confirmed 田野点 / 缺访谈对象时给出对应提示键。"""
    ctx = ResearchContext(target_location="喜洲")
    unconfirmed = phases.check_advance(
        "field_site_profiles", ctx, [FieldSiteRecord(name="喜洲")], [], False, False
    )
    assert not unconfirmed.can_advance
    assert set(unconfirmed.missing_fields) == {"confirmed_site", "informant"}

    site = FieldSiteRecord(name="喜洲", confirmed=True)
    no_informant = phases.check_advance("field_site_profiles", ctx, [site], [], False, False)
    assert not no_informant.can_advance
    assert "informant" in no_informant.missing_fields
    assert "confirmed_site" not in no_informant.missing_fields

    # 规格 §2.1 的 B→C 条件仅为"confirmed 田野点 + ≥1 访谈对象"，
    # 未填 target_location 不额外卡推进（迁移不得收紧旧引擎条件）
    no_target = phases.check_advance(
        "field_site_profiles",
        ResearchContext(),
        [site],
        [InformantRecord(code="P1", type="长老")],
        False,
        False,
    )
    assert no_target.can_advance and no_target.missing_fields == []


def test_check_advance_c_to_d_and_d_to_finished():
    r = phases.check_advance("itinerary_planning", ResearchContext(), [], [], False, False)
    assert not r.can_advance and "itinerary" in r.missing_fields
    ok = phases.check_advance("itinerary_planning", ResearchContext(), [], [], True, False)
    assert ok.can_advance and ok.to_phase == "proposal_generation"

    r2 = phases.check_advance("proposal_generation", ResearchContext(), [], [], True, False)
    assert not r2.can_advance and "proposal" in r2.missing_fields
    ok2 = phases.check_advance("proposal_generation", ResearchContext(), [], [], True, True)
    assert ok2.can_advance and ok2.to_phase == "finished"

    done = phases.check_advance("finished", ResearchContext(), [], [], True, True)
    assert not done.can_advance and done.to_phase is None


def test_extract_locations_respects_existing():
    loc = extract.extract_locations("我从北京出发去大理", {"target_location": "丽江"})
    assert loc == {"departure_city": "北京"}
    assert extract.extract_locations("随便聊聊", {}) == {}


def test_extract_researcher_info_maps_to_context_keys():
    info = extract.extract_researcher_info("我是做人类学的，团队调研，有基金支持，想去云南")
    assert info["discipline"] == "人类学"
    assert info["scale"] == "团队调研"
    assert info["budget"] == "基金支持"
    assert info["extras.region"] == "云南"
    assert extract.extract_researcher_info("今天天气不错") == {}


def test_informant_library_every_entry_complete():
    for key, entry in informants.INFORMANT_LIBRARY.items():
        assert entry["name"], key
        assert entry["approach"], key
        assert isinstance(entry["question_domains"], list) and entry["question_domains"], key
        assert all(isinstance(d, str) and d for d in entry["question_domains"])
        assert entry["ethics"], key
        assert entry["cautions"], key
    assert informants.get_informant("长老") is not None
    assert informants.get_informant("不存在的类型") is None
    assert informants.get_informant("") is None


def test_interview_guide_methodology_sections():
    text = informants.INTERVIEW_GUIDE_METHODOLOGY
    assert isinstance(text, str) and len(text) > 200
    for token in ("开场", "主问", "追问", "敏感", "知情同意"):
        assert token in text
