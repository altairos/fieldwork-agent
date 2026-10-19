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


def test_summarize_carries_answer_to_llm():
    """R1：answer 必须进 LLM 工具摘要——旧路径 items 分支提前 return 丢弃 answer，
    agent 只看到 total=0/top_items=[]，无法引用 AI 答案。"""
    from fieldwork_agent.kernel.models import Envelope, ItemPage
    from fieldwork_agent.runtime.loop import _summarize
    s = _summarize("search_ai", Envelope(ok=True, data=ItemPage(answer="## 答案", items=[])))
    assert s["answer"] == "## 答案"
    assert s["total"] == 0 and s["top_items"] == []


def test_summarize_without_answer_unchanged():
    from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem
    from fieldwork_agent.runtime.loop import _summarize
    s = _summarize("search_hotels", Envelope(ok=True, data=ItemPage(
        items=[SearchItem(name="客栈", price=180.0)], total=1)))
    assert "answer" not in s and s["total"] == 1
