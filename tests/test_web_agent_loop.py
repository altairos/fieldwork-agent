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


class ChatFakeLLM(FakeLLM):
    """带非流式 chat() 的 Fake：compose_proposal/compose_interview_guide
    走 llm.chat()（kernel.llm 契约）；返回空 content → 触发模板降级路径，
    测试不依赖真实 LLM。"""
    def chat(self, messages, **kw):
        return {"content": ""}


# F04：compose_proposal 完整四硬渠道参数（独立于 Task 2 的 NA 豁免路径）
_PROPOSAL_ARGS = {
    "ctx": {"discipline": "社会学", "topic": "宗族变迁", "scale": "单人调研",
            "budget": "2万", "time_window": "7月", "departure_city": "上海",
            "target_location": "大理"},
    "sites": [{"name": "大理", "confirmed": True, "profile": {"location": "大理"}}],
    "itinerary": {"legs": []},
    "informants": [{"code": "INF-01", "type": "宗族长老",
                    "relation": "同乡会引荐", "contact": "", "notes": ""}],
}


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


def test_llm_none_degrades_to_rule_reply(env):
    """无 LLM（未配置 DashScope key）降级：规则文案回复，回合正常完成不崩。

    对照 CLI 宿主 test_orchestrator_no_llm_rule_fallback：web loop 此前直接
    ``llm.chat_stream`` 触发 'NoneType' object has no attribute 'chat_stream'。
    """
    repo, tm = env
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, None, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "error" not in types, f"降级不应报错: {[e.data for e in events if e.event == 'error']}"
    assert types[-1] == "turn_done"
    deltas = [e.data["text"] for e in events if e.event == "message_delta"]
    assert "".join(deltas).strip(), "降级必须给出非空引导文案"
    msgs = repo.get_messages("p1")
    assert any(m["role"] == "assistant" and m["content"].strip() for m in msgs)


def test_llm_error_frame_emits_error_and_failed(env):
    """LLM 终止帧带 error（如 key 无效）：发 error 事件 + failed 终态，不静默空回复。

    回归：此前 error 帧 content="" 被 elif "content" 分支捕为终止帧，
    accumulated_reply 为空 → 静默 turn_done，配错 key 表现为「没反应」。
    """
    repo, tm = env
    llm = FakeLLM([[
        {"content": "", "tool_calls": None, "finish_reason": "", "usage": {},
         "error": "Invalid API-key provided"},
    ]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "error" in types, "LLM 错误必须透出，不可静默"
    assert "turn_done" not in types  # 失败终态不发 turn_done
    err = next(e for e in events if e.event == "error")
    assert "Invalid API-key" in err.data["message"]
    assert tm.get_turn("p1", turn.turn_id).status == "failed"
    msgs = repo.get_messages("p1")
    assert not any(m["role"] == "assistant" for m in msgs)  # 不落盘空助手消息


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


def test_user_message_not_duplicated_to_llm(env):
    """F09：当前用户消息只发一次给模型（不随 history 重复）。"""
    repo, tm = env
    llm = FakeLLM([[{"delta": "ok"}, {"content": "ok", "tool_calls": None,
                    "finish_reason": "stop", "usage": {}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "我要去大理", tm, llm, FakeFlyAI()))
    msgs = llm.calls[0][0]
    dup = [m for m in msgs if m["role"] == "user" and m["content"] == "我要去大理"]
    assert len(dup) == 1


def test_extraction_persists_research_context(env):
    """F03：对话中的研究信息轮末写回结构化状态（确定性抽取打底）。"""
    repo, tm = env
    llm = FakeLLM([[{"delta": "好"}, {"content": "好", "tool_calls": None,
                    "finish_reason": "stop", "usage": {}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id,
                         "我是社会学背景，从上海去大理调研", tm, llm, FakeFlyAI()))
    ctx = repo.get_research_context("p1")
    assert ctx.get("discipline") == "社会学"
    assert ctx.get("departure_city") == "上海"
    assert ctx.get("target_location") == "大理"


def test_compose_proposal_persisted_as_document(env):
    """F04：compose_proposal 成功后文档落盘（kind/version 如实记录）。"""
    repo, tm = env
    llm = ChatFakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "compose_proposal",
                                          "arguments": _PROPOSAL_ARGS}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "已生成"},
         {"content": "已生成", "tool_calls": None, "finish_reason": "stop",
          "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "生成方案", tm, llm, FakeFlyAI()))
    docs = repo.list_proposals("p1")
    assert len(docs) == 1
    assert docs[0]["kind"] == "proposal"
    doc = repo.get_proposal_by_id("p1", docs[0]["id"])
    assert doc["content"].startswith("# ")
    assert "## " in doc["content"]  # 八章降级结构


def test_compose_interview_guide_persisted(env):
    """F04：compose_interview_guide 的产物拼合落盘为 interview_guide 文档。"""
    repo, tm = env
    llm = ChatFakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "compose_interview_guide",
                                          "arguments": {"topic": "宗族变迁",
                                                        "informant_types": ["宗族长老"]}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "已生成"},
         {"content": "已生成", "tool_calls": None, "finish_reason": "stop",
          "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "生成访谈提纲", tm, llm, FakeFlyAI()))
    docs = repo.list_proposals("p1")
    assert len(docs) == 1
    assert docs[0]["kind"] == "interview_guide"
    doc = repo.get_proposal_by_id("p1", docs[0]["id"])
    assert "## 宗族长老" in doc["content"]


# ---------------------------------------------------------------- 快照链（spec §5.1）


def test_search_snapshot_persisted_and_turn_done_carries_ids(env):
    """回合内搜索即时落盘为快照；turn_done 事件携带 snapshot_ids。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "search_hotels",
                                          "arguments": {"dest_name": "大理"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "好"},
         {"content": "好", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "搜酒店", tm, llm, FakeFlyAI()))
    cached = repo.list_cached_searches("p1")
    assert len(cached) == 1 and cached[0]["tool"] == "search_hotels"
    assert "search_hotels" in cached[0]["query"] and "大理" in cached[0]["query"]
    done = next(e for e in tm.replay("p1", turn.turn_id, after_seq=0)
                if e.event == "turn_done")
    assert done.data["snapshot_ids"] == [cached[0]["id"]]


def test_dispatch_injects_live_snapshot_refs():
    """dispatch 对声明 snapshot_refs 形参的 fn 注入活字典当前内容（Task 11 消费）。"""
    from fieldwork_agent.kernel.models import Envelope
    from fieldwork_agent.kernel.registry import _REGISTRY, CapabilityMeta, register
    from fieldwork_agent.web_host.agent.dispatch import build_dispatch_fn
    from pydantic import BaseModel

    class ProbeArgs(BaseModel):
        pass

    def probe(*, snapshot_refs=None):
        return Envelope(ok=True, data={"refs": snapshot_refs})

    register(CapabilityMeta(name="probe_tool", description="p", fn=probe,
                            args_model=ProbeArgs))
    try:
        live = {"大理": "flyai 快照 srch_1（2026-10-16 查询）"}
        dispatch = build_dispatch_fn(None, None, live_snapshot_refs=live)
        env = dispatch("probe_tool", {})
        assert env.ok and env.data["refs"] == live
        live["喜洲"] = "flyai 快照 srch_2（2026-10-16 查询）"  # 活字典：后续调用可见
        assert dispatch("probe_tool", {}).data["refs"]["喜洲"]
    finally:
        _REGISTRY.pop("probe_tool", None)
