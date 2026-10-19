"""search_ai 字符串 data 修复（spec 设计 A，R1）。

生产实录 field_20261019_025042：ai-search 返回 data 为 Markdown 字符串，
_page_envelope 假设 dict → 'str' object has no attribute 'get' 崩溃，
经 dispatch 包成 internal_error，答案内容全部丢失。
"""
from fieldwork_agent.kernel.sources.flyai import _page_envelope


def test_string_data_keeps_answer():
    env = _page_envelope({"status": 0, "data": "## 民宿推荐\n- [甲](https://x.example)",
                          "systemMessage": "体验模式"})
    assert env.ok
    assert env.data.answer == "## 民宿推荐\n- [甲](https://x.example)"
    assert env.data.items == [] and env.data.total == 0
    assert env.degraded == "体验模式"


def test_string_data_empty_is_none_answer():
    env = _page_envelope({"status": 0, "data": ""})
    assert env.ok and env.data.answer is None


def test_dict_data_path_unchanged():
    env = _page_envelope({"status": 0, "data": {"itemList": []}})
    assert env.ok and env.data.total == 0 and env.data.answer is None


def test_string_data_no_crash_without_system_message():
    env = _page_envelope({"status": 0, "data": "纯文本答案"})
    assert env.ok and env.degraded is None
