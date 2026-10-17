# tests/test_context_patch.py
"""kernel/context_patch 共享严格校验（审计二 R05）。"""
import pytest
from fieldwork_agent.kernel.context_patch import (
    ALLOWED_CONTEXT_FIELDS,
    context_fields_hint,
    sanitize_context_patch,
)


def test_r05_valid_single_and_multi_fields():
    assert sanitize_context_patch({"budget": "5万"}) == {"budget": "5万"}
    out = sanitize_context_patch({"discipline": "人类学", "topic": "宗族"})
    assert out == {"discipline": "人类学", "topic": "宗族"}


def test_r05_strings_are_stripped():
    assert sanitize_context_patch({"topic": "  宗族变迁  "}) == {"topic": "宗族变迁"}


def test_r05_bool_value_kept_verbatim():
    out = sanitize_context_patch({"interviews_not_applicable": True,
                                  "interviews_na_reason": " 纯文献研究 "})
    assert out["interviews_not_applicable"] is True
    assert out["interviews_na_reason"] == "纯文献研究"


def test_r05_empty_or_non_dict_patch_rejected():
    with pytest.raises(ValueError, match="patch 为空"):
        sanitize_context_patch({})
    with pytest.raises(ValueError, match="patch 为空"):
        sanitize_context_patch(None)
    with pytest.raises(ValueError, match="patch 为空"):
        sanitize_context_patch("budget")


def test_r05_unknown_fields_listed_in_message():
    with pytest.raises(ValueError) as exc:
        sanitize_context_patch({"hacked": "x", "nope": "y"})
    assert "未知字段：hacked" in str(exc.value)
    assert "未知字段：nope" in str(exc.value)


def test_r05_non_string_value_rejected():
    with pytest.raises(ValueError, match="budget 须为字符串"):
        sanitize_context_patch({"budget": 5})


def test_r05_blank_string_rejected():
    with pytest.raises(ValueError, match="topic 不得为空白字符串"):
        sanitize_context_patch({"topic": "   "})


def test_r05_na_true_without_reason_rejected():
    with pytest.raises(ValueError, match="interviews_na_reason 需与"):
        sanitize_context_patch({"interviews_not_applicable": True})


def test_r05_na_true_with_reason_in_patch_accepted():
    out = sanitize_context_patch({"interviews_not_applicable": True,
                                  "interviews_na_reason": "纯文献研究"})
    assert out["interviews_not_applicable"] is True


def test_r05_na_true_with_current_reason_accepted():
    out = sanitize_context_patch({"interviews_not_applicable": True},
                                 current={"interviews_na_reason": "存量理由"})
    assert out["interviews_not_applicable"] is True


def test_r05_na_false_without_reason_accepted():
    out = sanitize_context_patch({"interviews_not_applicable": False})
    assert out["interviews_not_applicable"] is False


def test_r05_na_string_bool_rejected():
    with pytest.raises(ValueError, match="interviews_not_applicable 须为布尔"):
        sanitize_context_patch({"interviews_not_applicable": "true"})


def test_r05_atomic_rejection_collects_all_errors():
    with pytest.raises(ValueError) as exc:
        sanitize_context_patch({"budget": 5, "topic": " ", "hacked": "x"})
    msg = str(exc.value)
    assert "未知字段：hacked" in msg
    assert "budget 须为字符串" in msg
    assert "topic 不得为空白字符串" in msg


def test_r05_research_questions_whitelisted_and_stripped():
    """方案 B：深挖研究问题经 update_research_context 写入（三入口共用白名单）。"""
    assert "research_questions" in ALLOWED_CONTEXT_FIELDS
    out = sanitize_context_patch(
        {"research_questions": " 宗族礼生角色在丧仪中的实践 "})
    assert out == {"research_questions": "宗族礼生角色在丧仪中的实践"}


def test_r05_hint_lists_all_fields():
    hint = context_fields_hint()
    for field in ALLOWED_CONTEXT_FIELDS:
        assert field in hint
