# tests/test_web_agent_loop.py
import asyncio

import pytest
from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.turn_manager import TurnManager


class FakeLLM:
    """脚本化流式 LLM：chat_stream 按脚本 yield。"""
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


def test_simple_reply_emits_delta_and_turn_done(env):
    repo, tm = env
    llm = FakeLLM([[
        {"delta": "你好"}, {"delta": "世界"},
        {"content": "你好世界", "tool_calls": None, "finish_reason": "stop",
         "usage": {"total_tokens": 5}},
    ]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "message_delta" in types and types[-1] == "turn_done"
    deltas = [e.data["text"] for e in events if e.event == "message_delta"]
    assert "".join(deltas) == "你好世界"


def test_tool_call_emits_started_completed(env):
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "search_hotels",
                                          "arguments": {"dest_name": "大理"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "已找到"},
         {"content": "已找到", "tool_calls": None, "finish_reason": "stop",
          "usage": {"total_tokens": 20}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "搜酒店", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "tool_started" in types and "tool_completed" in types
    started = next(e for e in events if e.event == "tool_started")
    assert started.data["call_id"] == "c1" and started.data["tool"] == "search_hotels"
    completed = next(e for e in events if e.event == "tool_completed")
    assert completed.data["ok"] is True


def test_user_message_persisted_immediately(env):
    """R4：用户消息即时持久化，不等轮末。"""
    repo, tm = env
    llm = FakeLLM([[{"delta": "ok"}, {"content": "ok", "tool_calls": None,
                    "finish_reason": "stop", "usage": {}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "测试消息", tm, llm, FakeFlyAI()))
    msgs = repo.get_messages("p1")
    assert any(m["role"] == "user" and m["content"] == "测试消息" for m in msgs)
    assert any(m["role"] == "assistant" and m["content"] == "ok" for m in msgs)


def test_cancel_stops_loop(env):
    """取消后 loop 不再发新事件，终态为 cancelled。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "search_hotels",
                                          "arguments": {"dest_name": "X"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "不应到达"},
         {"content": "不应到达", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    tm.cancel("p1", turn.turn_id)  # 预取消
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    assert tm.get_turn("p1", turn.turn_id).status == "cancelled"
