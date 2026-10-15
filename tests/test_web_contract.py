# tests/test_web_contract.py
"""L3③ Web loop 契约测试（spec §6）+ R6 业务边界场景。

全 mock：FakeLLM 脚本化 + FakeFlyAI + tmp_path repo，不联网、无 key。
"""
import asyncio

import pytest
from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem, ToolError
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.turn_manager import TurnManager


class ScriptedLLM:
    """按脚本返回 chat_stream 帧序列。"""
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat_stream(self, messages, **kw):
        self.calls.append((list(messages), kw))
        return iter(self.script.pop(0))


class StubFlyAI:
    """8 搜索原语恒返回单条目成功包络。"""
    def __getattr__(self, name):
        def _stub(**kw):
            return Envelope(ok=True, data=ItemPage(
                items=[SearchItem(name=f"{name}-A", price=888.0, price_raw="¥888")],
                total=1, cheapest=888.0, dearest=888.0))
        return _stub


class DegradedFlyAI:
    """flyai 不可用降级。"""
    def __getattr__(self, name):
        def _stub(**kw):
            return Envelope(ok=False, error=ToolError(
                code="flyai_unavailable", message="CLI not found", hint="安装 flyai"))
        return _stub


@pytest.fixture
def web_env(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    tm = TurnManager(root=tmp_path / "projects")
    return repo, tm


async def _run(repo, pid, tid, text, tm, llm, flyai):
    from fieldwork_agent.web_host.agent.loop import run_turn
    await run_turn(repo, pid, tid, text, tm, llm, flyai)


def test_full_turn_event_sequence(web_env):
    """完整回合：tool_started → tool_completed → message_delta → turn_done，序号单调。"""
    repo, tm = web_env
    llm = ScriptedLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "search_hotels",
                                          "arguments": {"dest_name": "大理"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "找到"}, {"delta": "酒店"},
         {"content": "找到酒店", "tool_calls": None, "finish_reason": "stop",
          "usage": {"total_tokens": 30}}],
    ])
    turn = tm.create_turn("p1")
    asyncio.run(_run(repo, "p1", turn.turn_id, "搜大理住宿", tm, llm, StubFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "tool_started" in types
    assert "tool_completed" in types
    assert "message_delta" in types
    assert types[-1] == "turn_done"
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_iteration_cap_stops(web_env):
    """R5：超过 max_iters 后收敛。"""
    repo, tm = web_env
    tc_frame = [{"delta": ""},
                {"content": "", "tool_calls": [{"id": "x", "name": "search_ai",
                                                 "arguments": {"query": "q"}}],
                 "finish_reason": "tool_calls", "usage": {}}]
    final_frame = [{"delta": "总结"},
                   {"content": "总结", "tool_calls": None, "finish_reason": "stop",
                    "usage": {}}]
    llm = ScriptedLLM([tc_frame] * 10 + [final_frame])
    turn = tm.create_turn("p1")
    asyncio.run(_run(repo, "p1", turn.turn_id, "无限搜索", tm, llm, StubFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    tool_starts = [e for e in events if e.event == "tool_started"]
    assert len(tool_starts) <= 8
    assert events[-1].event == "turn_done"


def test_flyai_degraded_envelope(web_env):
    """R6：flyai 不可用 → tool_completed.ok=false，回合正常收尾。"""
    repo, tm = web_env
    llm = ScriptedLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "search_flights",
                                          "arguments": {"origin": "北京",
                                                        "destination": "大理"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "搜索不可用"},
         {"content": "搜索不可用", "tool_calls": None, "finish_reason": "stop",
          "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    asyncio.run(_run(repo, "p1", turn.turn_id, "搜航班", tm, llm, DegradedFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    completed = next(e for e in events if e.event == "tool_completed")
    assert completed.data["ok"] is False
    assert events[-1].event == "turn_done"


def test_idempotent_no_double_execution(web_env):
    """R4：同幂等键不重复执行。"""
    repo, tm = web_env
    llm = ScriptedLLM([[{"delta": "ok"}, {"content": "ok", "tool_calls": None,
                        "finish_reason": "stop", "usage": {}}]])
    t1 = tm.create_turn("p1", idempotency_key="idem-1")
    t2 = tm.create_turn("p1", idempotency_key="idem-1")
    assert t1.turn_id == t2.turn_id
    asyncio.run(_run(repo, "p1", t1.turn_id, "hi", tm, llm, StubFlyAI()))
    events = tm.replay("p1", t1.turn_id, after_seq=0)
    assert sum(1 for e in events if e.event == "turn_done") == 1


def test_replay_after_disconnect(web_env):
    """R4：断线后按 seq 回放不丢不重。"""
    repo, tm = web_env
    llm = ScriptedLLM([[{"delta": "a"}, {"delta": "b"}, {"delta": "c"},
                        {"content": "abc", "tool_calls": None,
                         "finish_reason": "stop", "usage": {}}]])
    turn = tm.create_turn("p1")
    asyncio.run(_run(repo, "p1", turn.turn_id, "hi", tm, llm, StubFlyAI()))
    all_events = tm.replay("p1", turn.turn_id, after_seq=0)
    replayed = tm.replay("p1", turn.turn_id, after_seq=2)
    assert all(e.seq > 2 for e in replayed)
    assert len(replayed) == len(all_events) - 2


def test_phase_transition_deterministic(web_env):
    """spec §3.3：阶段推进由 check_advance 确定性判定。"""
    repo, tm = web_env
    repo.update_research_context("p1", {
        "purpose": "开题", "discipline": "人类学", "topic": "宗族",
        "scale": "单人", "budget": "2万", "time_window": "三个月"})
    llm = ScriptedLLM([[{"delta": "好的"},
                        {"content": "好的", "tool_calls": None,
                         "finish_reason": "stop", "usage": {}}]])
    turn = tm.create_turn("p1")
    asyncio.run(_run(repo, "p1", turn.turn_id, "开始", tm, llm, StubFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    transitions = [e for e in events if e.event == "phase_transition"]
    assert len(transitions) == 1
    assert transitions[0].data["from"] == "research_positioning"
    assert transitions[0].data["to"] == "field_site_profiles"
