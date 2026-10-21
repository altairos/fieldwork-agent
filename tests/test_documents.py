"""documents/proposal 测试 — 提案与访谈提纲的 LLM/降级双路径，全部基于 mock 不联网。"""
import json as _json

import pytest
from fieldwork_agent.kernel import build_tool_schemas
from fieldwork_agent.kernel.documents.informants import suggest_informants
from fieldwork_agent.kernel.documents.proposal import (
    PROPOSAL_SECTIONS,
    _bullets_itinerary,
    _fmt_price,
    build_context_block,
    compose_interview_guide,
    compose_proposal,
)
from fieldwork_agent.kernel.models import InformantCandidate, ProposalDoc, ResearchContext

CTX = ResearchContext(discipline="社会学", topic="宗族变迁", scale="单人调研",
                      budget="2万", time_window="6-9月", target_location="喜洲")
SITES = [{"name": "喜洲", "confirmed": True, "profile": {"accommodation": "..."}}]
# 审计第二批 F12：非访谈研究（interviews_not_applicable）豁免访谈渠道硬条件
NA_CTX = ResearchContext(
    discipline="社会学", topic="历史文献研究", scale="单人调研",
    budget="2万", time_window="6-9月", target_location="喜洲",
    interviews_not_applicable=True,
    interviews_na_reason="采用档案与文献分析法，不需访谈对象",
)


class FakeLLM:
    """记录最近一次 chat 的 messages 与 kwargs——kwargs 用于钉死 spec 精确参数契约。"""

    def __init__(self, out):
        self.out, self.messages, self.kwargs = out, [], {}

    def chat(self, messages, **kw):
        self.messages = messages
        self.kwargs = kw
        return {"content": self.out, "tool_calls": None, "finish_reason": "stop", "usage": {}}


def test_context_block_contains_all_channels():
    block = build_context_block(CTX, SITES, {"legs": []}, [{"code": "P1", "type": "村干部"}],
                                [{"title": "旧笔记"}])
    for token in ["宗族变迁", "喜洲", "legs", "村干部", "旧笔记"]:
        assert token in block


def test_channel_absent_asserts():
    with pytest.raises(ValueError, match="缺少渠道"):
        build_context_block(CTX, [], {"legs": []}, [{"code": "P1"}], [])  # sites为空


def test_degrade_without_llm():
    doc = compose_proposal(CTX, SITES, {"legs": []}, [{"code": "P1"}], [], llm=None)
    assert doc.markdown.startswith("# ") and "## 四、详细行程安排" in doc.markdown
    assert len(doc.sections) == 8
    # 八章标题字面单一来源：降级输出的 ## 序列 == PROPOSAL_SECTIONS
    assert doc.sections == [f"## {h}" for h in PROPOSAL_SECTIONS]


def test_interview_guide_per_type():
    llm = FakeLLM("提纲正文")
    guides = compose_interview_guide("宗族变迁", ["村干部", "乡村教师"], llm=llm)
    assert set(guides) == {"村干部", "乡村教师"}
    assert "知情同意" in llm.messages[-1]["content"]
    # 钉死 guide 路径的 LLM 参数（spec 公理 2 精确值；两类报道人共用同一参数组）
    assert llm.kwargs["slot"] == "interview"
    assert llm.kwargs["max_tokens"] == 4096
    assert llm.kwargs["temperature"] == 0.5


def test_mode_default_and_fallback():
    doc = compose_proposal(CTX, SITES, {"legs": []}, [{"code": "P1"}], [], llm=None)
    assert doc.mode == "global" and doc.requested_mode == "global"
    assert len(doc.inputs_hash) == 40  # R3：供宿主做确认/失效判定
    doc2 = compose_proposal(CTX, SITES, {"legs": []}, [{"code": "P1"}], [], llm=None,
                            mode="kaiti")
    # 审阅意见 R1：请求体例与实际生成体例分开；回退提示头部前置而非仅尾注
    assert doc2.requested_mode == "kaiti" and doc2.mode == "global"
    assert doc2.markdown.startswith("> ⚠")
    assert "待深化" in doc2.markdown


def test_llm_prompt_has_reference_redline():
    """审阅意见 R1：文献红线进 LLM 提示——只用所给条目，不得虚构。"""
    llm = FakeLLM("## 一、研究背景\n内容")
    compose_proposal(CTX, SITES, {"legs": []}, [{"code": "P1"}], [], llm=llm)
    assert "不得虚构" in llm.messages[-1]["content"]


def test_degrade_empty_knowledge_lists_retrieval_needs():
    """无文献输入 → 第八章输出待补检索清单而非臆测引用。"""
    doc = compose_proposal(CTX, SITES, {"legs": []}, [{"code": "P1"}], [], llm=None)
    assert "待补检索清单" in doc.markdown
    assert len(doc.sections) == 8  # 章数不变


# ────────────────────────────────────────────────────────────────
# 补充覆盖：LLM 成功路径 / 错误分支降级 / 未收录类型 / 注册面 / 单章重写
# ────────────────────────────────────────────────────────────────

# 与 phases.phase_prompt("proposal_generation") 中的八章模板一致，供 LLM 成功路径校验
_EIGHT = "\n".join([
    "# 研究方案：宗族变迁",
    "",
    "## 一、研究背景与问题提出",
    "内容 A",
    "## 二、田野点选择依据（含交通/住宿可行性分析）",
    "内容 B",
    "## 三、研究设计与方法论",
    "内容 C",
    "## 四、详细行程安排（含具体航班/车次/酒店信息）",
    "内容 D",
    "## 五、预算明细（基于实时搜索数据）",
    "内容 E",
    "## 六、风险评估与伦理预案",
    "内容 F",
    "## 七、预期成果",
    "内容 G",
    "## 八、参考文献",
    "内容 H",
])


def test_llm_success_extracts_sections_and_uses_phase_prompt():
    llm = FakeLLM(_EIGHT)
    doc = compose_proposal(
        CTX, SITES, {"legs": []}, [{"code": "P1", "type": "村干部"}], [], llm=llm
    )
    assert isinstance(doc, ProposalDoc) and doc.mode == "global"
    # sections 用正则 `^## .+` 提取 → 8 章标题（`# ` 一级标题不计入）
    assert len(doc.sections) == 8
    assert any("四、详细行程安排" in s for s in doc.sections)
    # system 消息 = phase_prompt("proposal_generation")，"方案生成"字样来自迁移原文
    assert llm.messages[0]["role"] == "system"
    assert "方案生成" in llm.messages[0]["content"]
    # user 消息 = context_block + 固定尾句"请生成完整研究方案"
    assert "请生成完整研究方案" in llm.messages[-1]["content"]
    assert "宗族变迁" in llm.messages[-1]["content"]
    # 钉死 brief 逐字规定的 LLM 参数契约（任何默认值漂移都应红在此处、而不是静默通过 134 全绿）
    assert llm.kwargs["slot"] == "proposal"
    assert llm.kwargs["max_tokens"] == 8192
    assert llm.kwargs["temperature"] == 0.5


def test_llm_error_content_falls_back_to_template():
    """controller 裁决：chat() 的 error 分支（content 空串）也走模板降级、不抛异常。"""

    class ErrLLM:
        def __init__(self):
            self.messages, self.kwargs = [], {}

        def chat(self, messages, **kw):
            self.messages = messages
            self.kwargs = kw
            return {"content": "", "tool_calls": None, "finish_reason": "",
                    "usage": {}, "error": "upstream 500"}

    llm = ErrLLM()
    doc = compose_proposal(
        CTX, SITES, {"legs": []}, [{"code": "P1", "type": "村干部"}], [], llm=llm
    )
    # 走模板降级路径：markdown 含 "（模板降级版）" 尾注 + 八章 sections
    assert "（模板降级版）" in doc.markdown
    assert len(doc.sections) == 8
    # P0-1：降级原因对机器可见（不再只藏在正文尾注里）
    assert doc.degraded and "LLM 调用失败" in doc.degraded
    assert "upstream 500" in doc.degraded
    # error 分支同样命中 _llm_full_proposal，参数契约与成功路径一致
    assert llm.kwargs["slot"] == "proposal"
    assert llm.kwargs["max_tokens"] == 8192
    assert llm.kwargs["temperature"] == 0.5


def test_llm_exception_falls_back_with_reason_and_envelope_degraded():
    """P0-1 验收：mock 一个抛异常的 LLM → compose_proposal 不裸抛、degraded 非空，
    且 proposal_handler 把同一标记抬到 Envelope（tool_completed 可见）。"""

    class BoomLLM:
        def chat(self, messages, **kw):
            raise TimeoutError("upstream timeout")

    from fieldwork_agent.kernel.documents.proposal import proposal_handler

    doc = compose_proposal(
        CTX, SITES, {"legs": []}, [{"code": "P1", "type": "村干部"}], [], llm=BoomLLM()
    )
    assert "（模板降级版）" in doc.markdown and len(doc.sections) == 8
    assert doc.degraded and "upstream timeout" in doc.degraded

    env = proposal_handler(
        CTX.model_dump(mode="json"), SITES, {"legs": []},
        [{"code": "P1", "type": "村干部"}], [], llm=BoomLLM()
    )
    assert env.ok and env.degraded and "LLM 调用失败" in env.degraded
    # data 里也带 degraded：宿主即使只透传 data，机器仍能识别
    assert env.data.degraded == env.degraded


def test_proposal_handler_without_llm_keeps_existing_degraded_wording():
    """无 key 路径兼容旧文案（下游可能有断言/展示依赖）。"""
    from fieldwork_agent.kernel.documents.proposal import proposal_handler

    env = proposal_handler(
        CTX.model_dump(mode="json"), SITES, {"legs": []},
        [{"code": "P1", "type": "村干部"}], [], llm=None
    )
    assert env.ok and env.degraded == "模板降级版（未接入 LLM）"


def test_mode_fallback_is_machine_visible():
    """P1-11：kaiti/grant 回退不只在正文头部声明，也要进 degraded——
    否则模型看不到“体例未实现”，会在回复里自由发挥成致歉说明。"""
    doc = compose_proposal(CTX, SITES, {"legs": []}, [{"code": "P1"}], [], llm=None,
                           mode="kaiti")
    assert doc.requested_mode == "kaiti" and doc.mode == "global"
    assert doc.degraded and "体例降级" in doc.degraded and "开题报告" in doc.degraded
    # 模板降级原因也不能被体例说明盖掉
    assert "模板降级版" in doc.degraded


def test_input_too_long_is_machine_readable():
    """P1-11：输入超长 → degraded 带 ``input_too_long``（而不是模糊的“调用失败”）。"""

    class TooLongLLM:
        def chat(self, messages, **kw):
            return {"content": "", "tool_calls": None, "finish_reason": "", "usage": {},
                    "error": "Range of input length should be [1, 6000]"}

    doc = compose_proposal(
        CTX, SITES, {"legs": []}, [{"code": "P1", "type": "村干部"}], [], llm=TooLongLLM()
    )
    assert doc.degraded and "input_too_long" in doc.degraded
    assert "（模板降级版）" in doc.markdown


def test_context_block_budget_compacts_nested_profile():
    """P1-11：嵌套明细（transport/journeys）不得整段 JSON 直嵌，且超预算处要明说省略。"""
    huge = {"transport": {f"leg{i}": {"detail": "x" * 400} for i in range(40)}}
    sites = [{"name": "喜洲", "profile": huge}]
    block = build_context_block(CTX, sites, {"legs": []},
                                [{"code": "P1", "type": "村干部"}], [])
    assert "喜洲" in block
    # 单个值被压缩：不再出现 40 段 400 字的明细全文
    assert block.count("xxxxxxxxxx") <= 20
    assert len(block) < 9000


def test_interview_guide_unmatched_type_still_includes_methodology():
    """未收录类型不阻断生成：user prompt 依然带方法论与知情同意话术字样。"""
    llm = FakeLLM("提纲")
    guides = compose_interview_guide("宗族变迁", ["不存在的类型"], llm=llm)
    assert set(guides) == {"不存在的类型"}
    assert "知情同意" in llm.messages[-1]["content"]
    # 未收录类型也应把方法论纳入 prompt，方便模型按通用框架起草
    assert "访谈方法要点（开源简版）" in llm.messages[-1]["content"]


def test_interview_guide_degrade_without_llm():
    guides = compose_interview_guide("宗族变迁", ["村干部"], llm=None)
    assert set(guides) == {"村干部"}
    # 无 llm 时走模板降级：仍产出含方法论与知情同意话术的可用文本
    assert "知情同意" in guides["村干部"]
    assert "提问域" in guides["村干部"]


def test_section_rewrite_replaces_only_target_block():
    """section 非空 → 仅重写该章；其余段落原样保留（配合 prior_markdown）。"""
    prior = (
        "# 研究方案\n\n"
        "## 一、研究背景与问题提出\n旧内容 A\n\n"
        "## 四、详细行程安排\n旧行程\n\n"
        "## 八、参考文献\n旧文献\n"
    )
    llm = FakeLLM("## 四、详细行程安排\n新行程：D1 抵达，D2 访谈")
    doc = compose_proposal(
        CTX, SITES, {"legs": []}, [{"code": "P1"}], [],
        llm=llm, section="四、详细行程安排", prior_markdown=prior,
    )
    assert "新行程" in doc.markdown and "旧行程" not in doc.markdown
    # 其他段落保持不变
    assert "旧内容 A" in doc.markdown and "旧文献" in doc.markdown
    # 单章重写复用同一 LLM 参数组（slot=proposal）
    assert llm.kwargs["slot"] == "proposal"
    assert llm.kwargs["max_tokens"] == 8192
    assert llm.kwargs["temperature"] == 0.5


def test_section_rewrite_failure_keeps_prior_markdown():
    """P3-3：单章重写失败不再整篇回落模板——保留 prior 正文，只标记该章未更新。"""

    class FailingLLM:
        def chat(self, messages, **kw):
            return {"content": "", "error": "upstream 500"}

    prior = ("# 研究方案\n\n"
             "## 一、研究背景与问题提出\n旧内容 A\n\n"
             "## 八、参考文献\n旧文献\n")
    doc = compose_proposal(
        CTX, SITES, {"legs": []}, [{"code": "P1"}], [],
        llm=FailingLLM(), section="一、研究背景与问题提出", prior_markdown=prior,
    )
    # 旧正文全保留（旧实现会把整篇替换成模板，等于“改一章、丢全文”）
    assert "旧内容 A" in doc.markdown and "旧文献" in doc.markdown
    assert doc.degraded and "单章未更新" in doc.degraded
    assert "一、研究背景与问题提出" in doc.degraded


def test_compose_proposal_registered():
    tool = [t for t in build_tool_schemas() if t["function"]["name"] == "compose_proposal"]
    assert tool, "compose_proposal 应已注册"
    props = tool[0]["function"]["parameters"]["properties"]
    assert {"ctx", "sites", "informants", "knowledge", "mode", "section"} <= set(props)
    # 注入参数不进 schema（Task 5/6 先例）
    assert "llm" not in props


def test_compose_interview_guide_registered():
    tool = [t for t in build_tool_schemas()
            if t["function"]["name"] == "compose_interview_guide"]
    assert tool, "compose_interview_guide 应已注册"
    props = tool[0]["function"]["parameters"]["properties"]
    assert {"topic", "informant_types"} <= set(props)
    assert "llm" not in props


# ────────────────────────────────────────────────────────────────
# suggest_informants（Task 7+ 动态生成）：前三个测试为 brief Step 1 逐字补漏；
# brief 块内的 import json as _json / suggest_informants / InformantCandidate
# 三行导入上移至文件顶部（避免 E402），测试体本身未改动。
# ────────────────────────────────────────────────────────────────

_CANDIDATES = _json.dumps([{"type": "社群骨干", "approach": "a", "cautions": "c",
    "question_domains": ["q"], "ethics": "e", "access_channel": "ac", "preparation": "p"}] * 4,
    ensure_ascii=False)


def test_suggest_informants_validates_schema():
    llm = FakeLLM(_CANDIDATES)
    env = suggest_informants("性与性别少数群体的社区网络", "社会学", llm=llm)
    assert env.ok and len(env.data) == 4
    assert all(isinstance(c, InformantCandidate) for c in env.data)
    assert "社群骨干" in llm.messages[-1]["content"] or True  # prompt 含主题
    assert "示范" in llm.messages[-1]["content"] or "村干部" in llm.messages[-1]["content"]  # few-shot 锚定


def test_suggest_informants_illegal_json():
    env = suggest_informants("x", llm=FakeLLM("不是JSON"))
    assert not env.ok and env.error.code == "generation_invalid"


def test_suggest_informants_no_key_degraded():
    env = suggest_informants("宗族变迁")
    assert env.ok and env.degraded and "参考起点" in env.degraded
    assert 0 < len(env.data) <= 6 and all(isinstance(c, InformantCandidate) for c in env.data)
    # P0-2：候选未落库，下一步必须保存——机器可读提示随包络返回。
    # P2-3：提示必须宿主中立（Web 用 save_informants、MCP 用 save_informant）。
    assert env.next_action and "保存访谈对象" in env.next_action
    assert "save_informants" not in env.next_action


# ---- suggest_informants 补充覆盖（注册面 / 槽位 / 条目级容错 / 上限）----


def test_suggest_informants_prompt_carries_topic_and_json_array_instruction():
    llm = FakeLLM(_CANDIDATES)
    suggest_informants("性与性别少数群体的社区网络", "社会学", llm=llm)
    user = llm.messages[-1]["content"]
    assert "性与性别少数群体的社区网络" in user  # 主题进 prompt（brief 的 or True 断言补实）
    assert "输出 JSON 数组" in user
    assert llm.kwargs["slot"] == "proposal"  # brief：注册能力用 proposal 槽


def test_suggest_informants_site_profile_in_prompt():
    llm = FakeLLM(_CANDIDATES)
    suggest_informants("宗族变迁", "社会学", {"accommodation": "喜洲客栈"}, llm=llm)
    assert "喜洲客栈" in llm.messages[-1]["content"]


def test_suggest_informants_drops_illegal_entries_keeps_valid():
    out = _json.dumps([
        {"type": "意见领袖", "approach": "a", "cautions": "c", "question_domains": ["q"],
         "ethics": "e", "access_channel": "ac", "preparation": "p"},
        "不是dict",
        42,
    ], ensure_ascii=False)
    env = suggest_informants("x", llm=FakeLLM(out))
    assert env.ok and len(env.data) == 1 and env.data[0].type == "意见领袖"


def test_suggest_informants_caps_at_six():
    one = {"type": "t", "approach": "a", "cautions": "c", "question_domains": ["q"],
           "ethics": "e", "access_channel": "ac", "preparation": "p"}
    many = _json.dumps([dict(one, type=f"t{i}") for i in range(9)], ensure_ascii=False)
    env = suggest_informants("x", llm=FakeLLM(many))
    assert env.ok and len(env.data) == 6


def test_suggest_informants_error_envelope_has_code_and_message():
    env = suggest_informants("x", llm=FakeLLM("完全不是JSON"))
    assert env.error.code and env.error.message  # 全局约束：error 包络必带 code+message


def test_suggest_informants_registered():
    tool = [t for t in build_tool_schemas()
            if t["function"]["name"] == "suggest_informants"]
    assert tool, "suggest_informants 应已注册"
    props = tool[0]["function"]["parameters"]["properties"]
    assert {"research_topic", "discipline", "site_profile"} <= set(props)
    assert "llm" not in props


# 审计第二批 F12：非访谈研究（interviews_not_applicable）豁免访谈渠道硬条件


def test_context_block_na_exempts_informant_channel():
    block = build_context_block(NA_CTX, SITES, {"legs": []}, [], [])
    assert "不适用访谈方法" in block
    assert "档案与文献分析法" in block


def test_context_block_still_raises_without_na_flag():
    with pytest.raises(ValueError, match="缺少渠道"):
        build_context_block(CTX, SITES, {"legs": []}, [], [])  # informants 为空且未声明 NA


def test_degrade_na_exempts_informant_section():
    doc = compose_proposal(NA_CTX, SITES, {"legs": []}, [], [], llm=None)
    assert len(doc.sections) == 8  # 八章降级版照常产出
    assert "不适用访谈方法" in doc.markdown


def test_bullets_itinerary_structured_with_booking_links():
    """D11：legs/stays 结构化渲染替代 dict repr；chosen 带 jump_url 时尾接 [预订](url)。"""
    itinerary = {
        "legs": [
            {"from": "昆明", "to": "大理", "date": "2026-11-01", "direction": "outbound",
             "chosen": {"name": "G29", "price": 350.0, "mode": "train", "jump_url": None},
             "alternatives": [{"name": "MU", "price": 600.0, "mode": "flight"}]},
            {"from": "大理", "to": "昆明", "date": "2026-11-10", "direction": "return",
             "chosen": None, "alternatives": []},
        ],
        "stays": [
            {"dest": "大理", "check_in": "2026-11-01", "check_out": "2026-11-10",
             "nights": 9, "nightly_price": 150.0,
             "chosen": {"name": "洱海边民宿", "price": 150.0, "mode": "民宿",
                        "jump_url": "https://hotel.example/minsu"},
             "alternatives": []},
        ],
        "generated_at": "2026-10-18T08:00:00",
    }
    lines = _bullets_itinerary(itinerary)
    assert lines[0] == "- 腿：昆明 → 大理（2026-11-01，去程）：G29 ¥350，备选 1 条"   # 无链接不加 [预订]
    assert lines[1] == "- 腿：大理 → 昆明（2026-11-10）：无可用价格，预算未计"        # 无 chosen 如实
    assert lines[2] == ("- 住宿（大理）2026-11-01 → 2026-11-10 · 9 晚："
                        "洱海边民宿 ¥150/晚 [预订](https://hotel.example/minsu)")
    assert lines[3] == "- **generated_at**：2026-10-18T08:00:00"                       # 其余键通用分支不动
    # 防御：空 legs/stays 与 None itinerary 的既有占位不回归（空 dict 也是 falsy → 同占位）
    assert _bullets_itinerary({}) == ["- （暂无行程数据）"]
    assert _bullets_itinerary(None) == ["- （暂无行程数据）"]
    assert _bullets_itinerary({"legs": [], "stays": []}) == ["- **legs**：[]", "- **stays**：[]"]
    # 防御：非 http(s) 协议的 jump_url 不渲染预订链接（markdown 注入面收紧）
    bad = {**itinerary, "stays": [{**itinerary["stays"][0],
           "chosen": {**itinerary["stays"][0]["chosen"], "jump_url": "javascript:alert(1)"}}]}
    assert not any("[预订]" in ln for ln in _bullets_itinerary(bad))
    # 驻留项：带 jump_url 的腿整行断言（此前仅 stay 路径覆盖整行拼接）
    leg_linked = {**itinerary, "legs": [
        {**itinerary["legs"][0],
         "chosen": {**itinerary["legs"][0]["chosen"], "jump_url": "https://train.example/g29"}},
        itinerary["legs"][1],
    ]}
    assert _bullets_itinerary(leg_linked)[0] == (
        "- 腿：昆明 → 大理（2026-11-01，去程）：G29 ¥350，备选 1 条"
        " [预订](https://train.example/g29)")


def test_fmt_price_never_scientific_notation():
    """驻留项：大额价格不落 :g 科学计数法（¥1.23457e+06 对用户不可读）。"""
    assert _fmt_price(1234567.0) == "¥1234567"
    assert _fmt_price(12345678.5) == "¥12345678.5"
    assert _fmt_price(350) == "¥350"
    assert _fmt_price(150.5) == "¥150.5"
    assert _fmt_price(0.0) == "¥0"
    assert _fmt_price("¥350") == "¥350"   # 非数字原样


def test_guide_prompt_carries_research_questions_and_discipline():
    """F01：_guide_prompt 携带 research_questions/discipline，非空才出节。"""
    from fieldwork_agent.kernel.documents.proposal import _guide_prompt
    p = _guide_prompt("宗族与乡村治理", "村干部",
                      research_questions="RQ1 宗族结构", discipline="社会学")
    assert "研究主题：宗族与乡村治理" in p
    assert "研究问题：RQ1 宗族结构" in p
    assert "学科取向：社会学" in p
    p2 = _guide_prompt("主题", "村干部")
    assert "研究问题：" not in p2 and "学科取向：" not in p2


def test_bullets_researcher_lists_rq_and_purpose():
    """F01：研究者渠道含研究问题/输出用途；topic 标签改为研究主题。"""
    from fieldwork_agent.kernel.documents.proposal import _bullets_researcher
    from fieldwork_agent.kernel.models import ResearchContext
    lines = _bullets_researcher(ResearchContext(
        topic="宗族与乡村治理", research_questions="RQ1", purpose="硕士论文"))
    assert "- **研究主题**：宗族与乡村治理" in lines
    assert "- **研究问题**：RQ1" in lines
    assert "- **输出用途**：硕士论文" in lines
