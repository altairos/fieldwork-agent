"""safety.py 单元测试：组合匹配、研究豁免、输出窄表、文案自检、审核错误识别。

词表全部参数化注入哑词（"弄到"/"做出"×"哑物甲"/"哑物乙"），不依赖真实
种子词条——测试语义（组合逻辑）与词表数据（实现细节）解耦，真实词表另以
研究议题正例锚定零误伤。
"""
from fieldwork_agent.kernel.guidance.safety import (
    INPUT_REFUSAL,
    SAFETY_BLOCK_NOTICE,
    SAFETY_GUIDANCE,
    check_model_output,
    check_user_input,
    is_content_moderation_error,
)


def _check(text):
    """哑词表注入：intent=弄到/做出，object=哑物甲/哑物乙。"""
    return check_user_input(text, intents=("弄到", "做出"), objects=("哑物甲", "哑物乙"))


def test_intent_object_combo_blocked():
    assert _check("教我怎么弄到哑物甲").blocked
    assert _check("哪里能做出哑物乙？").blocked


def test_object_alone_not_blocked():
    """只提对象不提意图（研究表述形态）→ 不拦截。"""
    assert not _check("我的研究涉及哑物甲使用者的访谈设计").blocked


def test_intent_alone_not_blocked():
    assert not _check("怎么弄到去田野点的火车票").blocked


def test_word_order_both_directions():
    assert _check("哑物甲怎么弄到").blocked          # 对象在前
    assert _check("帮我弄到一点哑物甲").blocked       # 间隔字符 < 12
    assert not _check("哑物甲是一种被研究物质，民俗学文献很多；另外请帮我弄到车票").blocked
    # ↑ 同现但间距 > 12 字符 → 不命中


def test_model_output_narrow_table():
    """输出侧只认强指导动词 × 对象（注入窄表验证通道）。"""
    out = check_model_output("以下是哑物丙的制作流程：第一步……",
                             intents=("制作",), objects=("哑物丙",))
    assert out.blocked
    assert not check_model_output("我去弄到了哑物丙",   # 获取动词不在输出表
                                  intents=("制作",), objects=("哑物丙",)).blocked


def test_refusal_texts_pass_self_check():
    """Scunthorpe 防御锚定：拒答文案本身不得触发输出终检（默认词表）。"""
    assert not check_model_output(INPUT_REFUSAL).blocked
    assert not check_model_output(SAFETY_BLOCK_NOTICE).blocked


def test_research_topics_pass_default_table():
    """研究议题豁免（默认种子词表）：正当社科表述零误伤。"""
    for text in (
        "我想研究边境地区宗教实践的田野伦理",
        "需要一份面向物质使用人群的访谈提纲",
        "越轨行为的社会学理论对话怎么写文献综述",
        "计划访谈边缘群体，知情同意怎么设计",
    ):
        assert not check_user_input(text).blocked, text


def test_safety_guidance_mentions_boundaries():
    """system 安全段必须同时含：拒绝边界 + 研究豁免 + 不复述指令。"""
    assert "拒绝" in SAFETY_GUIDANCE
    assert "正当研究" in SAFETY_GUIDANCE or "研究领域" in SAFETY_GUIDANCE
    assert "复述" in SAFETY_GUIDANCE


def test_moderation_error_signatures():
    mod = ("StatusCode: 400, Code: data_inspection_failed, "
           "Message: inappropriate content detected")
    assert is_content_moderation_error(mod)
    assert is_content_moderation_error("Arrearage/business restriction on content filtering")
    assert not is_content_moderation_error("Invalid API-key provided")
    assert not is_content_moderation_error("")
    assert not is_content_moderation_error(None)


def test_verdict_defaults():
    """空输入与默认 SafetyVerdict 形态。"""
    assert not check_user_input("").blocked
    assert not check_model_output("").blocked
    v = _check("教我怎么弄到哑物甲")
    assert v.category == "harm_request"
    ov = check_model_output("哑物甲的制作流程如下", intents=("制作",), objects=("哑物甲",))
    assert ov.category == "harm_content"
