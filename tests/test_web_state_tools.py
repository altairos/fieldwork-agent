# tests/test_web_state_tools.py
"""Web 回合受限状态写工具（审计 F03 方案②）：白名单过滤 + 回路集成。"""
import asyncio

import pytest
from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.agent.state_tools import build_state_tools
from fieldwork_agent.web_host.turn_manager import TurnManager


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat_stream(self, messages, **kw):
        self.calls.append((list(messages), kw))
        return iter(self.script.pop(0))


class FakeFlyAI:
    def __getattr__(self, name):
        def _stub(**kw):
            return Envelope(ok=True, data=ItemPage(
                items=[SearchItem(name=f"{name}-结果", price=100.0)],
                total=1, cheapest=100.0, dearest=100.0))
        return _stub


@pytest.fixture
def env(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    tm = TurnManager(root=tmp_path / "projects")
    return repo, tm


def test_r05_unknown_field_rejected_atomically(env):
    """审计二 R05：白名单外键 + 空白值 → 整次拒绝（原子），绝不部分写入。"""
    repo, _ = env
    schemas, handlers = build_state_tools(repo, "p1")
    assert [s["function"]["name"] for s in schemas] == ["update_research_context"]
    envl = handlers["update_research_context"](
        patch={"budget": "5万", "hacked": "x", "topic": "  "})
    assert not envl.ok
    assert envl.error.code == "invalid_arguments"
    assert "未知字段：hacked" in envl.error.message
    assert "topic 不得为空白字符串" in envl.error.message
    assert repo.get_research_context("p1").get("budget") is None


def test_bool_flag_persists(env):
    repo, _ = env
    _, handlers = build_state_tools(repo, "p1")
    envl = handlers["update_research_context"](
        patch={"interviews_not_applicable": True,
               "interviews_na_reason": "纯文献研究"})
    assert envl.ok
    ctx = repo.get_research_context("p1")
    assert ctx.get("interviews_not_applicable") is True
    assert ctx.get("interviews_na_reason") == "纯文献研究"


def test_empty_patch_rejected(env):
    repo, _ = env
    _, handlers = build_state_tools(repo, "p1")
    envl = handlers["update_research_context"](patch={"nope": 1})
    assert not envl.ok
    assert envl.error.code == "invalid_arguments"


def test_loop_state_tool_call_persists(env):
    """回路集成：LLM 发起 update_research_context 工具调用 → 状态落盘。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "update_research_context",
                                          "arguments": {"patch": {"budget": "5万",
                                                                   "scale": "两人同行"}}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "已更新"},
         {"content": "已更新", "tool_calls": None, "finish_reason": "stop",
          "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "预算改成5万，两人同行",
                         tm, llm, FakeFlyAI()))
    ctx = repo.get_research_context("p1")
    assert ctx.get("budget") == "5万" and ctx.get("scale") == "两人同行"


def test_r05_bool_type_strict(env):
    """审计二 R05：布尔字段收字符串 "true" → 拒绝（旧实现会当普通字符串收下）。"""
    repo, _ = env
    _, handlers = build_state_tools(repo, "p1")
    envl = handlers["update_research_context"](
        patch={"interviews_not_applicable": "true"})
    assert not envl.ok
    assert envl.error.code == "invalid_arguments"
    assert "interviews_not_applicable 须为布尔" in envl.error.message


def test_data_carries_updated_and_reason_for_card(env):
    """轮 2 症状②：data 携带 updated 字段名 + reason 依据，供前端状态卡渲染。

    前端 ToolCard 只消费 Envelope.data（Timeline 不传 args），reason 文本
    必须经 data 通道下发给卡片，否则状态写入只剩键名列表无依据说明。
    """
    repo, _ = env
    _, handlers = build_state_tools(repo, "p1")
    envl = handlers["update_research_context"](
        patch={"budget": "5万", "topic": "宗族礼生"}, reason="用户确认预算与主题")
    assert envl.ok
    assert envl.data["updated"] == ["budget", "topic"]
    assert envl.data["reason"] == "用户确认预算与主题"
    # reason 缺省时省略键：卡片不渲染空依据行，data 形状保持最小
    envl2 = handlers["update_research_context"](patch={"scale": "单人"})
    assert envl2.ok
    assert envl2.data == {"updated": ["scale"]}
