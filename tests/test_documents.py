"""documents/proposal 测试 — 提案与访谈提纲的 LLM/降级双路径，全部基于 mock 不联网。"""
import pytest
from fieldwork_agent.kernel import build_tool_schemas
from fieldwork_agent.kernel.documents.proposal import (
    PROPOSAL_SECTIONS,
    build_context_block,
    compose_interview_guide,
    compose_proposal,
)
from fieldwork_agent.kernel.models import ProposalDoc, ResearchContext

CTX = ResearchContext(discipline="社会学", topic="宗族变迁", scale="单人调研",
                      budget="2万", time_window="6-9月", target_location="喜洲")
SITES = [{"name": "喜洲", "confirmed": True, "profile": {"accommodation": "..."}}]


class FakeLLM:
    """记录最近一次 chat 的 messages 与 kwargs——kwargs 用于钉死 spec 精确参数契约。"""

    def __init__(self, out):
        self.out, self.messages, self.kwargs = out, [], {}

    def chat(self, messages, **kw):
        self.messages = messages
        self.kwargs = kw
        return {"content": self.out, "tool_calls": None, "finish_reason": "stop", "usage": {}}


def test_context_block_contains_all_channels():
    block = build_context_block(CTX, SITES, {"legs": []}, [{"code": "P1", "type": "长老"}],
                                [{"title": "旧笔记"}])
    for token in ["宗族变迁", "喜洲", "legs", "长老", "旧笔记"]:
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
    guides = compose_interview_guide("宗族变迁", ["长老", "集市商户"], llm=llm)
    assert set(guides) == {"长老", "集市商户"}
    assert "知情同意" in llm.messages[-1]["content"]
    # 钉死 guide 路径的 LLM 参数（spec 公理 2 精确值；两类报道人共用同一参数组）
    assert llm.kwargs["slot"] == "interview"
    assert llm.kwargs["max_tokens"] == 4096
    assert llm.kwargs["temperature"] == 0.5


def test_mode_default_and_fallback():
    doc = compose_proposal(CTX, SITES, {"legs": []}, [{"code": "P1"}], [], llm=None)
    assert doc.mode == "global"
    doc2 = compose_proposal(CTX, SITES, {"legs": []}, [{"code": "P1"}], [], llm=None,
                            mode="kaiti")
    assert doc2.mode == "kaiti" and "待深化" in doc2.markdown  # 回退 global 提纲+提示


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
        CTX, SITES, {"legs": []}, [{"code": "P1", "type": "长老"}], [], llm=llm
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
        CTX, SITES, {"legs": []}, [{"code": "P1", "type": "长老"}], [], llm=llm
    )
    # 走模板降级路径：markdown 含 "（模板降级版）" 尾注 + 八章 sections
    assert "（模板降级版）" in doc.markdown
    assert len(doc.sections) == 8
    # error 分支同样命中 _llm_full_proposal，参数契约与成功路径一致
    assert llm.kwargs["slot"] == "proposal"
    assert llm.kwargs["max_tokens"] == 8192
    assert llm.kwargs["temperature"] == 0.5


def test_interview_guide_unmatched_type_still_includes_methodology():
    """未收录类型不阻断生成：user prompt 依然带方法论与知情同意话术字样。"""
    llm = FakeLLM("提纲")
    guides = compose_interview_guide("宗族变迁", ["不存在的类型"], llm=llm)
    assert set(guides) == {"不存在的类型"}
    assert "知情同意" in llm.messages[-1]["content"]
    # 未收录类型也应把方法论纳入 prompt，方便模型按通用框架起草
    assert "半结构式访谈方法论要点" in llm.messages[-1]["content"]


def test_interview_guide_degrade_without_llm():
    guides = compose_interview_guide("宗族变迁", ["长老"], llm=None)
    assert set(guides) == {"长老"}
    # 无 llm 时走模板降级：仍产出含方法论与知情同意话术的可用文本
    assert "知情同意" in guides["长老"]
    assert "提问域" in guides["长老"]


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
