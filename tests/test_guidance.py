"""guidance 子包测试 — 阶段推进纯函数、消息提取、访谈对象库（全部纯数据，不联网不调 LLM）。"""

from fieldwork_agent.kernel.guidance import extract, informants, phases
from fieldwork_agent.kernel.guidance.extract import (
    apply_message_extractions,
    extract_time_window,
)
from fieldwork_agent.kernel.models import (
    FieldSiteRecord,
    InformantCandidate,
    InformantRecord,
    ResearchContext,
)


def test_check_advance_a_to_b_needs_topic_and_research_question():
    """定位探索只要求主题与已明确的问题；预算/人数等不阻止学术探索。"""
    six = dict(purpose="kaiti", discipline="人类学", topic="宗族", scale="单人",
               budget="2万", time_window="7月")
    # 六基础字段齐但缺深挖研究问题 → 不推进，且 missing 指明缺口
    no_deep = phases.check_advance(
        "research_positioning", ResearchContext(**six), [], [], False, False)
    assert not no_deep.can_advance
    assert "research_questions" in no_deep.missing_fields
    # 七字段全齐（深挖字段走本体槽位）→ 推进
    full = phases.check_advance(
        "research_positioning", ResearchContext(research_questions="宗族礼生角色", **six),
        [], [], False, False)
    assert full.can_advance and full.to_phase == "field_site_profiles"
    # 深挖字段走 extras 同名兜底同样计入（与旧引擎 info 字典语义一致）
    via_extras = phases.check_advance(
        "research_positioning",
        ResearchContext(extras={"research_questions": "宗族礼生角色"}, **six), [], [], False, False)
    assert via_extras.can_advance
    r2 = phases.check_advance(
        "research_positioning", ResearchContext(discipline="人类学", topic="x"), [], [], False, False
    )
    assert not r2.can_advance and r2.missing_fields == ["research_questions"]
    minimal = phases.check_advance("research_positioning", ResearchContext(
        topic="宗族", research_questions="礼生如何参与事务协商"), [], [], False, False)
    assert minimal.can_advance


def test_check_advance_b_to_c_requires_site_and_informant():
    site = FieldSiteRecord(name="喜洲", confirmed=True)
    ctx = ResearchContext(target_location="喜洲")
    no_informant = phases.check_advance("field_site_profiles", ctx, [site], [], False, False)
    assert not no_informant.can_advance, "B→C 缺访谈对象必须不推进(spec §2.1)"
    ok = phases.check_advance(
        "field_site_profiles", ctx, [site], [InformantRecord(code="P1", type="村干部")], False, False
    )
    assert ok.can_advance and ok.to_phase == "itinerary_planning"


def test_extract_locations_from_message():
    loc = extract.extract_locations("我从北京出发去大理调研白族村落", {})
    assert loc == {"target_location": "大理", "departure_city": "北京"}


def test_informant_library_shape():
    assert len(informants.INFORMANT_LIBRARY) >= 3
    one = informants.get_informant("村干部") or next(iter(informants.INFORMANT_LIBRARY.values()))
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
    assert phases.REQUIRED_FIELDS["research_positioning"] == ["topic", "research_questions"]
    assert "target_location" in phases.REQUIRED_FIELDS["field_site_profiles"]
    assert phases.REQUIRED_FIELDS["finished"] == []


def test_check_advance_extras_can_fill_positioning_field():
    """字段本体为空但 extras 同名有值时同样计数（与旧引擎 info 字典语义一致）。"""
    ctx = ResearchContext(
        purpose="course", discipline="社会学", topic="乡绅", scale="单人",
        time_window="寒假", extras={"budget": "自费", "research_questions": "乡绅治理"}
    )
    r = phases.check_advance("research_positioning", ctx, [], [], False, False)
    assert r.can_advance and r.to_phase == "field_site_profiles"


def test_research_questions_survives_model_validate():
    """深挖字段是 ResearchContext 正式槽位：宿主 check_and_advance 经
    ``model_validate(repo dict)`` 构造 ctx，未声明键会被 Pydantic 丢弃，
    字段必须声明才能计入推进（数据链防丢锚点）。"""
    ctx = ResearchContext.model_validate(
        {"topic": "宗族", "research_questions": "礼生角色的仪式实践"})
    assert ctx.research_questions == "礼生角色的仪式实践"
    assert ctx.extras == {}


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
        [InformantRecord(code="P1", type="村干部")],
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
    # 审阅意见 R3：草稿已生成≠用户确认，D→finished 必须 proposal_confirmed
    draft_only = phases.check_advance("proposal_generation", ResearchContext(), [], [],
                                      True, True)
    assert not draft_only.can_advance
    assert "proposal_confirmation" in draft_only.missing_fields
    ok2 = phases.check_advance("proposal_generation", ResearchContext(), [], [], True, True,
                               proposal_confirmed=True)
    assert ok2.can_advance and ok2.to_phase == "finished"

    done = phases.check_advance("finished", ResearchContext(), [], [], True, True,
                                proposal_confirmed=True)
    assert not done.can_advance and done.to_phase is None


def test_check_advance_b_to_c_non_interview_method():
    """审阅意见 R3：非访谈型研究可显式跳过访谈对象硬条件。"""
    site = FieldSiteRecord(name="县档案馆", confirmed=True)
    ctx = ResearchContext(interviews_not_applicable=True, interviews_na_reason="档案研究为主")
    r = phases.check_advance("field_site_profiles", ctx, [site], [], False, False)
    assert r.can_advance and r.to_phase == "itinerary_planning"


def test_positioning_prompt_and_questions_ask_purpose_first():
    """审阅意见 R1：用途采集前置到阶段 A。"""
    assert "purpose" not in phases.REQUIRED_FIELDS["research_positioning"]
    qs = phases.guiding_questions("research_positioning")
    assert "用途" in qs[0] or "场合" in qs[0]
    assert "用途" in phases.phase_prompt("research_positioning")


def test_positioning_prompt_deep_digs_research_interest():
    """从具体线索协助形成核心问题，保留确认写入规则而不要求用户凑数量。"""
    prompt = phases.phase_prompt("research_positioning")
    assert "具体经历" in prompt and "一个核心问题" in prompt
    assert "已经清楚就沿用" in prompt
    assert "一次只问 2-3 个问题" not in prompt
    # 深挖成果经结构化字段沉淀（供 check_advance 七字段门槛计数）
    assert "research_questions" in prompt
    assert "update_research_context" in prompt
    # 引导问题模板含同款深挖问句
    qs = phases.guiding_questions("research_positioning")
    assert any("具体经历" in q and "材料" in q for q in qs)
    assert not any("2-3" in q for q in qs)


def test_prompts_are_honest_about_scope():
    """审阅意见 R1：删除"基金委"过度承诺字样；文献红线入 D 阶段提示词。"""
    for p in phases.PHASES:
        assert "基金委" not in phases.phase_prompt(p)
    assert "虚构" in phases.phase_prompt("proposal_generation")


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


def test_extract_purpose():
    """审阅意见 R1：用途关键词提取；"基金支持"只填 budget 不得误判为 grant。"""
    assert extract.extract_researcher_info("明年三月要开题报告")["purpose"] == "kaiti"
    assert extract.extract_researcher_info("准备申报国家社科项目活页")["purpose"] == "grant"
    assert extract.extract_researcher_info("我要申请海外的phd项目")["purpose"] == "global"
    assert extract.extract_researcher_info("课程作业要求写proposal")["purpose"] == "course"
    assert "purpose" not in extract.extract_researcher_info("今天天气不错")
    info = extract.extract_researcher_info("我是做人类学的，有基金支持")
    assert info.get("budget") == "基金支持" and "purpose" not in info


def test_informant_library_every_entry_complete():
    for key, entry in informants.INFORMANT_LIBRARY.items():
        assert entry["approach"], key
        assert isinstance(entry["question_domains"], list) and entry["question_domains"], key
        assert all(isinstance(d, str) and d for d in entry["question_domains"])
        assert entry["ethics"], key
        assert entry["cautions"], key
        # get_informant 返回含 access_channel/preparation 的完整档案（田野保障方案数据源）
        assert entry["access_channel"], key
        assert entry["preparation"], key
    assert informants.get_informant("村干部") is not None
    assert informants.get_informant("不存在的类型") is None
    assert informants.get_informant("") is None


def test_interview_guide_methodology_sections():
    text = informants.INTERVIEW_GUIDE_METHODOLOGY
    assert isinstance(text, str) and len(text) > 200
    for token in ("开场", "主问", "追问", "敏感", "知情同意"):
        assert token in text


# ---------------------------------------------------------------- 精选示范词条与主题匹配


def test_informant_library_entries_have_examples():
    """LIBRARY 每类带 2-3 条提问示例，末条为追问句式（few-shot 质量锚的数据基础）。"""
    for key, entry in informants.INFORMANT_LIBRARY.items():
        ex = entry["examples"]
        assert isinstance(ex, list) and 2 <= len(ex) <= 3, key
        assert all(isinstance(s, str) and s for s in ex), key
        assert ex[-1].startswith("追问"), key
    one = informants.get_informant("村干部")
    assert one is not None and one["examples"]  # get_informant 兼容：深拷贝后仍含 examples


def test_informant_examples_shape():
    """INFORMANT_EXAMPLES：精选 5-6 条示范词条（brief task-4 逐字形状）。"""
    assert len(informants.INFORMANT_EXAMPLES) >= 3          # 示范库是质量锚非枚举白名单
    one = informants.get_informant("村干部") or next(iter(informants.INFORMANT_EXAMPLES.values()))
    assert one["approach"] and one["question_domains"] and one["ethics"]
    assert "access_channel" in one and "preparation" in one  # 进入田野途径与行前保障(田野保障方案数据源)


def test_informant_examples_are_curated_candidate_archives():
    """精选集是 LIBRARY 的类型覆盖子集，每条值本身即 InformantCandidate 可 validate 的档案 dict。"""
    assert len(informants.INFORMANT_EXAMPLES) == 3
    assert set(informants.INFORMANT_EXAMPLES) <= set(informants.INFORMANT_LIBRARY)
    for key, archive in informants.INFORMANT_EXAMPLES.items():
        assert archive["type"] == key
        assert archive["access_channel"] and archive["preparation"]  # 逐类实质文案，非统一兜底句
        assert archive["access_channel"] != archive["preparation"]
        assert archive["examples"]
        c = InformantCandidate.model_validate(archive)
        assert c.type == key and c.question_domains and c.access_channel


def test_match_examples_returns_candidate_validatable_dicts():
    hits = informants.match_examples("少数民族村落宗族组织变迁")
    assert hits and all("type" in h and "ethics" in h for h in hits)
    for h in hits:
        c = InformantCandidate.model_validate(h)  # 形状 = InformantCandidate 字段可 validate
        assert c.type and c.approach and c.question_domains and c.ethics
        # access_channel/preparation 为精选词条自带的逐类实质文案（不再是统一兜底句）
        assert c.access_channel and c.preparation


def test_match_examples_empty_topic_returns_all():
    """空主题全返——恢复 brief task-4 逐字等值语义。"""
    assert informants.match_examples("") == list(informants.INFORMANT_EXAMPLES.values())


def test_match_examples_garbage_topic_falls_back_to_all():
    """全部落空的主题 → 兜底返回全部示范词条（降级路径宁给参考起点也不给空结果）。"""
    # "饕餮貔貅" 的二字组在精选集 approach/question_domains/examples 中必然零命中
    assert informants.match_examples("饕餮貔貅") == list(informants.INFORMANT_EXAMPLES.values())

# ────────────────────────────────────────────────────────────────
# 审计第二批 F03：time_window 采集 + 组合抽取器（CLI/Web 共用）
# ────────────────────────────────────────────────────────────────


def test_extract_time_window_patterns():
    assert extract_time_window("我打算2026年3月到2026年6月去调研") == {
        "time_window": "2026年3月到2026年6月"}
    assert extract_time_window("计划2026年7月出发") == {"time_window": "2026年7月"}
    assert extract_time_window("大概3月到5月之间成行") == {"time_window": "3月到5月"}
    assert extract_time_window("就9月去吧") == {"time_window": "9月"}
    assert extract_time_window("我暑假去") == {"time_window": "暑假"}
    assert extract_time_window("寒假期间过去") == {"time_window": "寒假期间"}
    assert extract_time_window("时间还没定") == {}


def test_apply_message_extractions_combines_and_respects_existing():
    existing = {"discipline": "人类学", "time_window": "暑假"}
    found = apply_message_extractions("我是社会学背景，从上海去大理调研", existing)
    # discipline 已填（人类学）不被消息里的"社会学"覆盖
    assert "discipline" not in found
    assert found.get("departure_city") == "上海"
    assert found.get("target_location") == "大理"
    # time_window 已填同样不覆盖
    assert "time_window" not in found


def test_apply_message_extractions_collects_when_empty():
    found = apply_message_extractions("社会学，2026年3月去，预算2万", {})
    assert found.get("discipline") == "社会学"
    assert found.get("time_window") == "2026年3月"
    assert found.get("budget") == "2万"


def test_apply_message_extractions_extras_dotted_key_respects_existing():
    existing = {"extras": {"region": "西南"}}
    found = apply_message_extractions("我想去华南看看", existing)
    assert "extras.region" not in found  # extras.region 已填不覆盖
    found2 = apply_message_extractions("我想去华南看看", {})
    assert found2.get("extras.region") == "华南"
