"""F08：知情同意话术不得承诺不可控结果（公开版 + 私有版同规则）。"""
import os

from fieldwork_agent.kernel import assets

FORBIDDEN = ["不会给村里带来什么麻烦", "绝对匿名", "绝对保密", "零风险",
             "我马上删", "不影响您任何", "不影响其权益"]
PUBLIC_MUST = ["没法做到完全匿名", "只承诺研究者可控"]


def test_public_methodology_no_overpromise():
    text = assets.interview_methodology()
    for bad in FORBIDDEN:
        assert bad not in text, f"公开版出现过度承诺：{bad}"
    for need in PUBLIC_MUST:
        assert need in text, f"公开版缺诚实表述：{need}"


def test_private_methodology_same_rule():
    path = os.path.join(os.path.dirname(assets.__file__), "..",
                        "secrets", "interview_methodology.txt")
    path = os.path.abspath(path)
    if not os.path.exists(path):       # 开源检出不带 secrets：跳过不失败
        import pytest
        pytest.skip("secrets 不在本检出")
    text = open(path, encoding="utf-8").read()
    for bad in FORBIDDEN:
        assert bad not in text, f"私有版出现过度承诺：{bad}"


def test_guide_prompt_still_mentions_consent():
    """F08 改动不破坏 guide prompt 契约：知情同意字样仍在。"""
    from fieldwork_agent.kernel.guidance import informants
    assert "知情同意" in informants.INTERVIEW_GUIDE_METHODOLOGY
