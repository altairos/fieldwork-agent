# tests/test_web_agent_loop.py
import asyncio
import json
import re

import pytest
from fieldwork_agent.kernel.models import Envelope, GeoLocation, ItemPage, SearchItem
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


class FakeAMap:
    """替身 AMapClient：记录方法调用，返回固定成功包络（N0 通道注入钉子）。"""
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def weather(self, **kw):
        self.calls.append(("weather", kw))
        return Envelope(ok=True, data={"city": kw.get("city", "")})


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


def test_empty_final_reply_converges_with_retry(env):
    """症状③兜底：最终轮空正文（思考模型只想不写）不再静默结束——
    追加 system 收敛指令重取一次（不带工具），重试正文如实发出。"""
    repo, tm = env
    llm = FakeLLM([
        [{"content": "", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
        [{"delta": "阶段性总结"},
         {"content": "阶段性总结", "tool_calls": None, "finish_reason": "stop",
          "usage": {"total_tokens": 3}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "error" not in types
    assert types[-1] == "turn_done"
    deltas = "".join(e.data["text"] for e in events if e.event == "message_delta")
    assert deltas == "阶段性总结"
    assert len(llm.calls) == 2
    conv_messages, conv_kw = llm.calls[1]
    assert conv_messages[-1]["role"] == "system"   # 收敛指令已追加
    assert "不要再调用工具" in conv_messages[-1]["content"]
    assert conv_kw.get("tools") is None             # 收敛重试不带工具
    msgs = repo.get_messages("p1")
    assert any(m["role"] == "assistant" and m["content"] == "阶段性总结"
               for m in msgs)


def test_empty_reply_exhausted_emits_fallback(env):
    """收敛重试仍空（两轮都只想不写）：发兜底文案 + 正常完成，绝不静默空回合。"""
    repo, tm = env
    llm = FakeLLM([
        [{"content": "", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
        [{"content": "", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "error" not in types
    assert types[-1] == "turn_done"
    deltas = [e.data["text"] for e in events if e.event == "message_delta"]
    assert deltas and "未生成文字回复" in deltas[-1]
    msgs = repo.get_messages("p1")
    assert any(m["role"] == "assistant" and "未生成文字回复" in m["content"]
               for m in msgs)


def test_budget_hit_converges_gracefully(env):
    """预算触顶（max_iters=1）不再裸退出：system 收敛指令 + 总结正文 + completed。

    对齐 CLI runtime/loop.py 的触顶收敛（追 system 提示再取一次最终回答）。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "search_hotels",
                                          "arguments": {"dest_name": "大理"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "基于现有信息的总结"},
         {"content": "基于现有信息的总结", "tool_calls": None,
          "finish_reason": "stop", "usage": {"total_tokens": 8}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "搜酒店", tm, llm, FakeFlyAI(),
                         max_iters=1))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "error" not in types
    assert types[-1] == "turn_done"
    deltas = "".join(e.data["text"] for e in events if e.event == "message_delta")
    assert "基于现有信息的总结" in deltas
    conv_messages = llm.calls[1][0]
    assert conv_messages[-1]["role"] == "system"
    assert "上限" in conv_messages[-1]["content"]
    assert tm.get_turn("p1", turn.turn_id).status == "completed"


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


class CancelMidStreamLLM:
    """流式中途取消：首帧 delta 流出后、终帧前触发 cancel（P1-4 场景）。"""
    def __init__(self, tm, pid, tid):
        self._tm, self._pid, self._tid = tm, pid, tid
        self.calls = []

    def chat_stream(self, messages, **kw):
        self.calls.append((list(messages), kw))
        def _gen():
            yield {"delta": "部分"}
            self._tm.cancel(self._pid, self._tid)
            yield {"content": "残缺回复", "tool_calls": None,
                   "finish_reason": "stop", "usage": {}}
        return _gen()


def test_cancel_skips_end_of_turn_side_effects(env):
    """P1-4：流途中取消的回合，轮末副作用全部不落地。

    取消即中止本轮全部状态变更：残缺 assistant 回复不入历史、对话中的
    研究信息不被抽取写回、阶段不推进；终态仍为 cancelled。
    """
    repo, tm = env
    turn = tm.create_turn("p1")
    llm = CancelMidStreamLLM(tm, "p1", turn.turn_id)
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id,
                         "我想研究大理的宗族变迁，单人调研，预算2万",
                         tm, llm, FakeFlyAI()))
    assert tm.get_turn("p1", turn.turn_id).status == "cancelled"
    # 残缺回复不入消息史（只留用户消息）
    assert [m["role"] for m in repo.get_messages("p1", limit=0)] == ["user"]
    # 取消轮的抽取不写回
    assert not repo.get_research_context("p1").get("topic")


class CancelThenToolCallLLM:
    """N13：首帧 delta 流出后取消，随后终帧携带 tool_calls——取消后不得再启动工具。"""
    def __init__(self, tm, pid, tid):
        self._tm, self._pid, self._tid = tm, pid, tid
        self.calls = []

    def chat_stream(self, messages, **kw):
        self.calls.append((list(messages), kw))
        def _gen():
            yield {"delta": "部分"}
            self._tm.cancel(self._pid, self._tid)   # 消费中途取消
            yield {"content": "", "tool_calls": [{"id": "c1", "name": "search_hotels",
                                                  "arguments": {"dest_name": "大理"}}],
                   "finish_reason": "tool_calls", "usage": {}}
        return _gen()


def test_cancel_mid_stream_prevents_tool_start(env):
    """N13：慢流首帧后 cancel、终帧携带 tool_calls → 不再出现新的 tool_started，
    终态 cancelled（旧实现取消后仍会启动工具、发 tool_started）。"""
    repo, tm = env
    turn = tm.create_turn("p1")
    llm = CancelThenToolCallLLM(tm, "p1", turn.turn_id)
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "搜酒店", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "tool_started" not in types
    assert "tool_completed" not in types
    assert tm.get_turn("p1", turn.turn_id).status == "cancelled"


def test_deadline_during_stream_stops_before_tools(env):
    """N13：wall 截止在终帧到达前已过 → 启动工具前不再发起工具副作用。

    复现审计场景（wall=20ms、单帧同步 sleep）：已开始的阻塞 sleep 不可撤回
    （无硬截止），但越过截止后绝不再启动新工具（无 tool_started）。"""
    import time
    repo, tm = env

    class SlowThenToolCallLLM:
        def __init__(self):
            self.calls = []

        def chat_stream(self, messages, **kw):
            self.calls.append((list(messages), kw))
            def _gen():
                yield {"delta": "思考中"}
                time.sleep(0.08)   # 慢流：终帧迟迟才到，其间越过 wall 截止
                yield {"content": "",
                       "tool_calls": [{"id": "c1", "name": "search_hotels",
                                       "arguments": {"dest_name": "大理"}}],
                       "finish_reason": "tool_calls", "usage": {}}
            return _gen()

    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "搜酒店", tm,
                         SlowThenToolCallLLM(), FakeFlyAI(), max_wall_seconds=0.02))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "tool_started" not in types      # 越过 wall 截止后不启动工具
    assert "tool_completed" not in types


class DeleteProjectMidStreamLLM:
    """流式中途删除项目（审查 1.2 竞态）：首帧 delta 后执行 DELETE 路由三步
    （cancel_all → repo.delete_project → tm.purge_project），随后按 trailing_deltas
    决定是否继续产出迟到 delta，最后产出终帧。"""
    def __init__(self, repo, tm, pid, trailing_deltas=()):
        self._repo, self._tm, self._pid = repo, tm, pid
        self._trailing = list(trailing_deltas)

    def chat_stream(self, messages, **kw):
        def _gen():
            yield {"delta": "部分"}
            self._tm.cancel_all(self._pid)
            self._repo.delete_project(self._pid)
            self._tm.purge_project(self._pid)
            for text in self._trailing:
                yield {"delta": text}
            yield {"content": "残缺回复", "tool_calls": None,
                   "finish_reason": "stop", "usage": {}}
        return _gen()


def test_project_deleted_mid_stream_late_delta_no_crash(env):
    """审查 1.2：删除后仍有迟到 delta 到达——流内事件发射前必须检测回合
    已被清除，不得让 append_event 的 KeyError 逃逸成未处理任务异常。"""
    repo, tm = env
    turn = tm.create_turn("p1")
    llm = DeleteProjectMidStreamLLM(repo, tm, "p1", trailing_deltas=("迟到",))
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))


def test_project_deleted_mid_stream_end_of_turn_no_crash(env):
    """审查 1.2：删除后仅终帧到达——轮末持久化（append_message/抽取/推进）
    与其 error 兜底都不得对已清除的回合抛 KeyError。"""
    repo, tm = env
    turn = tm.create_turn("p1")
    llm = DeleteProjectMidStreamLLM(repo, tm, "p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))


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


def test_snapshot_carries_query_key_and_parent_call_id(env):
    """R9/R13/R14：快照落盘带归一化 query_key 与产生它的工具 call_id。"""
    repo, tm = env
    turn = tm.create_turn("p1")
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "search_hotels",
                                          "arguments": {"dest_name": "大理"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "好"},
         {"content": "好", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "搜酒店", tm, llm, FakeFlyAI()))
    entries = repo.list_cached_searches("p1")
    assert entries, "search_hotels 成功调用应落快照"
    e = entries[0]
    assert e["turn_id"] == turn.turn_id
    assert e["parent_call_id"]            # 非空：来自 tool_started 的 call_id
    assert e["query_key"].startswith("search_hotels|{")
    # 归一化：query_key 的参数含签名默认值（FakeFlyAI.search_hotels 的默认形参）


def test_query_key_canonical_helper():
    from fieldwork_agent.web_host.agent.loop import _query_key
    k1 = _query_key("search_hotels", {"dest_name": "大理", "check_in": ""})
    k2 = _query_key("search_hotels", {"check_in": "", "dest_name": "大理"})
    assert k1 == k2 == 'search_hotels|{"check_in": "", "dest_name": "大理"}'


class TwoPriceFlyAI:
    """R08 测试桩：search_hotels 按是否带 check_in 返回 11.0/22.0 两档价。

    参数驱动而非共享计数器——行程住宿调用走线程池，计数器 += 有线程竞争。
    """
    def search_hotels(self, *, dest_name="", check_in="", check_out="", **kw):
        price = 11.0 if not check_in else 22.0
        return Envelope(ok=True, data=ItemPage(
            items=[SearchItem(name="客栈A", price=price)],
            total=1, cheapest=price, dearest=price))

    def __getattr__(self, name):
        def stub(**kw):
            price = 100.0 if name == "search_trains" else 300.0
            return Envelope(ok=True, data=ItemPage(
                items=[SearchItem(name=f"{name}X", price=price)],
                total=1, cheapest=price, dearest=price))
        return stub


def test_itinerary_quotes_bound_to_live_snapshots(env):
    """R08 金标准：行程预算里的 @live: 规格键在轮末被替换为真实快照引用。

    直查（无日期）11.0 与行程内部带日期 22.0 的键天然不同——行程条目
    只应绑定行程自己内部调用的快照。6 天昆明→大理：夜数 5、
    researcher_count=1 → rooms_eff=1，住宿 = 22×5×1 = 110.0；
    交通两腿各 100.0（train 100 < flight 300，民宿/酒店同价 prefer 胜出）。
    """
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "search_hotels",
                                          "arguments": {"dest_name": "大理"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c2", "name": "compose_itinerary",
                                          "arguments": {"departure": "昆明",
                                                        "destinations": ["大理"],
                                                        "start_date": "2026-11-01",
                                                        "duration_days": 6,
                                                        "budget_total": 5000}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "好"},
         {"content": "好", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "查酒店并排行程", tm,
                         llm, TwoPriceFlyAI()))
    index = repo.list_cached_searches("p1")
    snaps = {c["id"]: c for c in index}
    itin_cid = next(c["id"] for c in index if c["tool"] == "compose_itinerary")
    plan = repo.get_cached_search("p1", itin_cid)["result"]
    items = plan["budget"]["items"]

    stay = next(b for b in items if b["name"].startswith("住宿"))
    assert stay["amount"] == 110.0
    stay_cid = re.search(r"flyai 快照 ([^\s（]+)", stay["source_ref"]).group(1)
    assert snaps[stay_cid]["tool"] == "search_hotels"
    assert repo.get_cached_search("p1", stay_cid)["result"]["cheapest"] == 22.0

    transports = [b for b in items if b["name"].startswith("交通")]
    assert len(transports) == 2
    for t in transports:
        assert t["amount"] == 100.0
        leg_cid = re.search(r"flyai 快照 ([^\s（]+)", t["source_ref"]).group(1)
        assert snaps[leg_cid]["tool"] == "search_trains"
        assert repo.get_cached_search("p1", leg_cid)["result"]["cheapest"] == 100.0

    assert "@live:" not in json.dumps(plan, ensure_ascii=False)
    old = [c for c in index if c["tool"] == "search_hotels"
           and repo.get_cached_search("p1", c["id"])["result"]["cheapest"] == 11.0]
    assert old and all(c["id"] != stay_cid for c in old)


class SeqHotelPriceFlyAI:
    """N05：search_hotels 带日期时按调用序返回 11、22……（Lock 保护，行程
    内部搜索走线程池）；无日期直查返回固定价。其余搜索固定价。"""
    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._n = 0

    def search_hotels(self, *, dest_name="", check_in="", check_out="", **kw):
        if not check_in:
            price = 99.0
        else:
            with self._lock:
                price = 11.0 * (self._n + 1)
                self._n += 1
        return Envelope(ok=True, data=ItemPage(
            items=[SearchItem(name="客栈A", price=price)],
            total=1, cheapest=price, dearest=price))

    def __getattr__(self, name):
        def stub(**kw):
            price = 100.0 if name == "search_trains" else 300.0
            return Envelope(ok=True, data=ItemPage(
                items=[SearchItem(name=f"{name}X", price=price)],
                total=1, cheapest=price, dearest=price))
        return stub


def test_n05_same_spec_two_itineraries_bind_own_snapshots(env):
    """N05：同规格（同 dest/日期）连续两次 compose_itinerary，酒店价 11→22；
    每份行程的住宿引用必须绑定自己那次的快照，而非都被后写覆盖成第二次。"""
    repo, tm = env
    itin_args = {"departure": "昆明", "destinations": ["大理"],
                 "start_date": "2026-11-01", "duration_days": 3,
                 "budget_total": 5000}
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "compose_itinerary",
                                          "arguments": dict(itin_args)}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c2", "name": "compose_itinerary",
                                          "arguments": dict(itin_args)}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "好"},
         {"content": "好", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "排两次同样行程", tm,
                         llm, SeqHotelPriceFlyAI()))
    index = repo.list_cached_searches("p1")
    itins = [c["id"] for c in index if c["tool"] == "compose_itinerary"]
    assert len(itins) == 2, itins
    stays = []
    for cid in itins:
        plan = repo.get_cached_search("p1", cid)["result"]
        stay = next(b for b in plan["budget"]["items"]
                    if b["name"].startswith("住宿"))
        ref_cid = re.search(r"flyai 快照 ([^\s（]+)", stay["source_ref"]).group(1)
        cheapest = repo.get_cached_search("p1", ref_cid)["result"]["cheapest"]
        stays.append((stay["amount"], cheapest, ref_cid))
    # 两份行程引用不同快照（未被后写覆盖成同一个）
    assert len({c for _, _, c in stays}) == 2, stays
    # 两份用了不同酒店价（11 与 22）
    assert len({c for _, c, _ in stays}) == 2, stays
    # 引用与实付一致：amount/cheapest 系数相同（= 夜数×房间数）；
    # 若被错绑到另一次的快照，两份系数会不同（旧实现：11 价行程引 22 快照）
    ratios = {round(amt / cheap, 6) for amt, cheap, _ in stays}
    assert len(ratios) == 1, stays


# ---------------------------------------------------------------- N5 amap 快照链（宿主侧）


class SnapshotAMap:
    """N5 测试桩：全方法成功，data 与真实 AMapClient 同构
    （geocode→GeoLocation、around→ItemPage、transit/weather→dict）。"""

    def geocode(self, address="", city=""):
        return Envelope(ok=True, data=GeoLocation(
            location="100.196,25.047", city="大理白族自治州", adcode="532901"))

    def around_search(self, keywords="", location="", radius=5000, page_size=10):
        return Envelope(ok=True, data=ItemPage(
            items=[SearchItem(name=f"{keywords}点", price=0.0)],
            total=1, cheapest=0.0, dearest=0.0))

    def direction_transit(self, origin="", destination="", city="", cityd="",
                          date=""):
        return Envelope(ok=True, data={"transits": []})

    def weather(self, city="", extensions="all"):
        return Envelope(ok=True, data={"city": city})


def test_amap_live_key_matches_kernel_refs():
    """N5 契约：宿主 _live_key amap 分支与 kernel amap_refs 纯函数逐字符一致
    （radius/city 等副参不进键，date 缺省留空段）；非契约方法（weather）无键。"""
    from fieldwork_agent.kernel.aggregation.amap_refs import (
        live_around_ref,
        live_geocode_ref,
        live_transit_ref,
    )
    from fieldwork_agent.web_host.agent.loop import _live_key
    assert _live_key("geocode", {"address": "喜洲", "city": "大理"}) == \
        live_geocode_ref("喜洲")
    assert _live_key("around_search", {"keywords": "卫生院 诊所",
                                       "location": "100.196,25.047",
                                       "radius": 5000}) == \
        live_around_ref("卫生院 诊所", "100.196,25.047")
    assert _live_key("direction_transit", {"origin": "102.71,25.03",
                                           "destination": "100.19,25.04",
                                           "date": "2026-11-02"}) == \
        live_transit_ref("102.71,25.03", "100.19,25.04", "2026-11-02")
    assert _live_key("direction_transit", {"origin": "102.71,25.03",
                                           "destination": "100.19,25.04"}) == \
        live_transit_ref("102.71,25.03", "100.19,25.04")
    assert _live_key("weather", {"city": "大理"}) == ""


def test_amap_drain_snapshots_position_args_and_label(env):
    """N5：_drain_snapshots amap 分支——geocode 位置传参（last_mile 形态）与
    around 关键字传参（site_profile 形态）都进快照；method 名带 amap_ 前缀；
    live_refs 文案「amap 快照」；weather 无键但有快照；游标幂等。"""
    from fieldwork_agent.kernel.sources.flyai import RecordingClient
    from fieldwork_agent.web_host.agent.loop import _drain_snapshots
    repo, _tm = env
    rec = RecordingClient(SnapshotAMap())
    rec.geocode("喜洲")  # 位置传参：RecordingClient 记 args=["喜洲"]、kwargs={}
    rec.around_search(keywords="卫生院 诊所", location="100.196,25.047")
    rec.weather(city="大理")
    snapshot_ids, live_refs = [], {}
    cursor = _drain_snapshots(repo, "p1", rec, snapshot_ids, live_refs,
                              0, turn_id="t1", label="amap")
    index = repo.list_cached_searches("p1")
    assert {c["tool"] for c in index} == \
        {"amap_geocode", "amap_around_search", "amap_weather"}
    geo_row = next(c for c in index if c["tool"] == "amap_geocode")
    assert "喜洲" in geo_row["query"]  # 位置参并入参数视图，query 可读
    assert live_refs["@live:amap_geocode:喜洲"].startswith("amap 快照")
    assert live_refs["@live:amap_around_search:卫生院 诊所|100.196,25.047"] \
        .startswith("amap 快照")
    assert not any(k.startswith("@live:amap_weather") for k in live_refs)
    assert len(snapshot_ids) == 3 and cursor == len(rec.calls)
    _drain_snapshots(repo, "p1", rec, snapshot_ids, live_refs, cursor,
                     turn_id="t1", label="amap")
    assert len(repo.list_cached_searches("p1")) == 3  # 幂等不重复落盘


def test_amap_profile_live_refs_replaced_by_snapshots(env):
    """N5 金标准：profile_field_site 附的 live_refs 规格键在轮末被替换为
    「amap 快照 {cid}」真实引用，@live: 键零残留；快照与 snapshot_ids 均在。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "profile_field_site",
                                          "arguments": {"location": "喜洲"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "好"},
         {"content": "好", "tool_calls": None, "finish_reason": "stop",
          "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "画像喜洲", tm, llm,
                         FakeFlyAI(), amap_client=SnapshotAMap()))
    profile = repo.get_state("p1")["field_sites"][0]["profile"]
    refs = profile["live_refs"]
    assert refs and all(r.startswith("amap 快照 ") for r in refs)
    tools = {c["tool"] for c in repo.list_cached_searches("p1")}
    assert {"amap_geocode", "amap_around_search"} <= tools
    done = next(e for e in tm.replay("p1", turn.turn_id, after_seq=0)
                if e.event == "turn_done")
    assert done.data["snapshot_ids"]
    assert "@live:" not in json.dumps(profile, ensure_ascii=False)



# ------------------------------------------- D6 规格键字典精确匹配（audit3 终审）


def test_resolve_live_refs_matches_keys_with_fullwidth_parens():
    r"""D6：含全角括号/空白的目的地名键不再被正则静默漏配——逐键精确子串替换。

    旧正则 ``@live:[A-Za-z_]+:[^\s（）()]+`` 在「杭州（余杭）」的全角括号处截断，
    键永不匹配 → 原键残留；字典精确匹配后键体可为任意字符。
    """
    from fieldwork_agent.web_host.agent.loop import _resolve_live_refs
    live_refs = {"@live:search_hotels:杭州（余杭）|2026-11-01|2026-11-03":
                 "flyai 快照 c9（2026-10-18 查询）"}
    data = {"budget": {"items": [
        {"source_ref": "@live:search_hotels:杭州（余杭）|2026-11-01|2026-11-03"}]}}
    out = _resolve_live_refs(data, live_refs)
    assert out["budget"]["items"][0]["source_ref"] == "flyai 快照 c9（2026-10-18 查询）"
    assert "@live:" not in json.dumps(out, ensure_ascii=False)


def test_resolve_live_refs_longest_key_first_avoids_prefix_truncation():
    """D6：hotel_types 尾段键是其裸键前缀——按键长降序替换，长键胜出无截断。"""
    from fieldwork_agent.web_host.agent.loop import _resolve_live_refs
    live_refs = {"@live:search_hotels:大理|d1|d2": "ref-bare",
                 "@live:search_hotels:大理|d1|d2|民宿,客栈": "ref-guesthouse"}
    key = "@live:search_hotels:大理|d1|d2|民宿,客栈"
    out = _resolve_live_refs({"ref": key}, live_refs)
    assert out["ref"] == "ref-guesthouse"          # 长键先替换，未被裸键前缀截断


# ------------------------------------------- 时窗回写（spec §5.3，final-review Fix-2）


class WindowLLM(FakeLLM):
    """流式脚本驱动对话；非流式 chat 返回节庆 JSON（analyze 节庆抽取契约）。"""

    def __init__(self, script, chat_content):
        super().__init__(script)
        self._chat_content = chat_content

    def chat(self, messages, **kw):
        return {"content": self._chat_content}


_ANALYZE_ARGS = {
    "departure": "昆明", "destination": "西昌",
    "range_start": "2026-07-01", "range_end": "2026-07-15",
    "duration_days": 3, "budget_total": 5000.0, "sample_step": 7,
}


def test_analyze_window_writes_candidate_windows_not_constraint(env):
    """F03：推荐时窗写 candidate_windows，time_window（用户约束）保持为空。"""
    repo, tm = env
    llm = WindowLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "analyze_fieldwork_window",
                                         "arguments": dict(_ANALYZE_ARGS)}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "好"},
         {"content": "好", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ], chat_content='[{"name": "火把节", "approx_start": "2026-07-05",'
                    ' "approx_end": "2026-07-06", "approx_dates": "农历六月廿四",'
                    ' "relevance": "r"}]')
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    ctx = repo.get_research_context("p1")
    assert ctx.get("time_window", "") == ""
    wins = ctx["candidate_windows"]
    assert wins and wins[0]["start"] == "2026-07-01" and wins[0]["end"] == "2026-07-03"
    assert "估算 ¥850" in wins[0]["reason"]
    # 时窗快照本身仍落盘（既有行为不变）
    cached = repo.list_cached_searches("p1")
    assert any(c["tool"] == "analyze_fieldwork_window" for c in cached)


def test_dispatch_injects_amap_client():
    """N0：amap 原语经 dispatch 收到宿主注入的 AMapClient 实例（非自建）——
    web 宿主对齐 CLI orchestrator/MCP host_ctx 的三通道注入。"""
    from fieldwork_agent.web_host.agent.dispatch import build_dispatch_fn

    fake = FakeAMap()
    dispatch = build_dispatch_fn(None, None, amap_client=fake)
    env = dispatch("amap_weather", {"city": "大理"})
    assert env.ok is True
    assert fake.calls and fake.calls[0][0] == "weather"
    assert fake.calls[0][1].get("city") == "大理"


def test_run_turn_passes_amap_client(env):
    """N0：run_turn 的 amap_client 形参透传到 dispatch 注入链。"""
    repo, tm = env
    fake = FakeAMap()
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "amap_weather",
                                          "arguments": {"city": "大理"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "ok"},
         {"content": "ok", "tool_calls": None, "finish_reason": "stop",
          "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "查天气", tm, llm, FakeFlyAI(),
                         amap_client=fake))
    assert fake.calls and fake.calls[0][1].get("city") == "大理"
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    completed = next(e for e in events if e.event == "tool_completed")
    assert completed.data["ok"] is True


def test_analyze_window_empty_windows_leaves_time_window_untouched(env, monkeypatch):
    """windows 为空/首窗缺日期时不回写：不抛错、time_window 保持原值。"""
    repo, tm = env
    repo.update_research_context("p1", {"time_window": "原有：7 月中旬"})
    from fieldwork_agent.web_host.agent import loop as agent_loop

    def _fake_build_dispatch_fn(flyai_client, llm, amap_client=None):
        def dispatch(name, args):
            return Envelope(ok=True, data={"windows": []})
        return dispatch

    monkeypatch.setattr(agent_loop, "build_dispatch_fn", _fake_build_dispatch_fn)
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "analyze_fieldwork_window",
                                         "arguments": dict(_ANALYZE_ARGS)}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "ok"},
         {"content": "ok", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    types = [e.event for e in tm.replay("p1", turn.turn_id, after_seq=0)]
    assert "error" not in types
    assert repo.get_research_context("p1")["time_window"] == "原有：7 月中旬"


# ------------------------------- state tools 缺参容错（轮 4 实测：卡死根因）


def test_state_tool_missing_arg_degrades_not_fails(env):
    """LLM 调 update_research_context 漏传 patch（实测 args={}）：
    此前 ``(**args)`` 的 TypeError 炸穿 gather → 整轮 error+failed，
    已流出的正文全部丢失且 LLM 无从自救。期望：折成
    invalid_arguments 信封发给 LLM，下一轮补参重试，回合正常完成。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1",
                                          "name": "update_research_context",
                                          "arguments": {}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "好的"},
         {"content": "好的", "tool_calls": None, "finish_reason": "stop",
          "usage": {"total_tokens": 5}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "error" not in types, "缺参不应炸掉整轮"
    assert types[-1] == "turn_done"
    completed = next(e for e in events if e.event == "tool_completed")
    assert completed.data["ok"] is False
    assert completed.data["error"]["code"] == "invalid_arguments"
    assert "patch" in completed.data["error"]["message"]


def test_state_tool_missing_arg_recovers_with_patch(env):
    """同场景的自救闭环：LLM 收到 invalid_arguments 后补 patch 重试成功
    （研究上下文真被写入），回合完成。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1",
                                          "name": "update_research_context",
                                          "arguments": {}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c2",
                                          "name": "update_research_context",
                                          "arguments": {"patch": {"topic": "滇剧传承"}}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "已记录"},
         {"content": "已记录", "tool_calls": None, "finish_reason": "stop",
          "usage": {"total_tokens": 6}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "研究滇剧", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "error" not in types and types[-1] == "turn_done"
    completed = [e for e in events if e.event == "tool_completed"]
    assert len(completed) == 2
    assert completed[0].data["ok"] is False      # 首次缺参被拒
    assert completed[1].data["ok"] is True       # 补参重试成功
    assert repo.get_research_context("p1").get("topic") == "滇剧传承"


def test_r05_finalize_failure_sets_failed(env, monkeypatch):
    """审计二 R05：轮末持久化异常 → error 事件 + failed 终态，绝不悬挂 running。"""
    repo, tm = env
    import fieldwork_agent.web_host.agent.loop as loop_mod

    def boom(*args, **kwargs):
        raise RuntimeError("抽取崩溃")

    monkeypatch.setattr(loop_mod, "_apply_extractions", boom)
    llm = FakeLLM([[{"delta": "好"}, {"content": "好", "tool_calls": None,
                    "finish_reason": "stop", "usage": {"total_tokens": 5}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "turn_done" not in types
    errs = [e for e in events if e.event == "error"]
    assert errs and errs[-1].data["code"] == "internal_error"
    assert "轮末持久化失败" in errs[-1].data["message"]
    assert tm.get_turn("p1", turn.turn_id).status == "failed"


# ------------------------------------------- 自动命名（任务1：title 槽位）


class AutonameLLM:
    """非流式 chat 桩（自动命名专用）：记录调用，返回脚本化名字或抛错。"""

    def __init__(self, content="", error=None):
        self.content, self.error = content, error
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append((list(messages), kw))
        if self.error:
            raise self.error
        return {"content": self.content}


def test_autoname_llm_writes_stripped_short_title(env):
    """任务1：title 空且 llm 返回名 → 首行去引号写入（≤30 字，extract 槽无工具）。"""
    repo, tm = env
    repo.append_message("p1", "user", "我想去大理研究宗族变迁")
    llm = AutonameLLM(content='"大理宗族变迁调研”')
    from fieldwork_agent.web_host.agent.loop import _maybe_autoname
    _maybe_autoname(repo, "p1", llm)
    title = repo.get_research_context("p1").get("title")
    assert title == "大理宗族变迁调研"
    assert len(title) <= 30
    assert llm.calls and llm.calls[0][1].get("slot") == "extract"
    assert llm.calls[0][1].get("tools") is None


def test_autoname_long_output_truncated_to_30(env):
    """任务1：LLM 名字超长 → 截断 30 字。"""
    repo, tm = env
    repo.append_message("p1", "user", "hi")
    llm = AutonameLLM(content="超" * 50)
    from fieldwork_agent.web_host.agent.loop import _maybe_autoname
    _maybe_autoname(repo, "p1", llm)
    assert repo.get_research_context("p1").get("title") == "超" * 30


def test_autoname_existing_title_never_overwritten(env):
    """任务1：用户已手动命名 → agent 永不覆盖（llm 根本不被调用）。"""
    repo, tm = env
    repo.update_research_context("p1", {"title": "我的手动名字"})
    llm = AutonameLLM(content="自动名")
    from fieldwork_agent.web_host.agent.loop import _maybe_autoname
    _maybe_autoname(repo, "p1", llm)
    assert repo.get_research_context("p1").get("title") == "我的手动名字"
    assert llm.calls == []


def test_autoname_does_not_overwrite_rename_during_window(env):
    """N09：命名窗口内用户改名 → llm.chat 返回后复核 title 非空，放弃覆盖。"""
    repo, tm = env
    repo.append_message("p1", "user", "我想去大理研究宗族变迁")

    class RenamingLLM:
        def chat(self, messages, **kw):
            repo.update_research_context("p1", {"title": "用户手动改名"})   # 模拟并发改名
            return {"content": "自动生成的名字"}

    from fieldwork_agent.web_host.agent.loop import _maybe_autoname
    _maybe_autoname(repo, "p1", RenamingLLM())
    assert repo.get_research_context("p1").get("title") == "用户手动改名"


def test_autoname_skips_when_project_edited_during_window(env):
    """N09：命名窗口内项目被编辑（updated_at 变、title 仍空）→ 放弃写入，
    避免用过期上下文命名覆盖用户的后续意图。"""
    repo, tm = env
    repo.append_message("p1", "user", "去大理研究宗族")

    class EditingLLM:
        def chat(self, messages, **kw):
            repo.update_research_context("p1", {"topic": "白族本主信仰"})   # 改 topic，title 仍空
            return {"content": "自动名字"}

    from fieldwork_agent.web_host.agent.loop import _maybe_autoname
    _maybe_autoname(repo, "p1", EditingLLM())
    assert not str(repo.get_research_context("p1").get("title") or "").strip()


async def test_schedule_autoname_is_fire_and_forget(env):
    """N09：_schedule_autoname 登记后台任务即返回、不同步命名——主回合不等待；
    排空后台任务后 title 才落地。"""
    repo, tm = env
    repo.append_message("p1", "user", "我想去大理研究宗族变迁")
    llm = AutonameLLM(content="大理宗族调研")
    from fieldwork_agent.web_host.agent import loop as loop_mod
    loop_mod._schedule_autoname(repo, "p1", llm)
    # 尚未 await：任务未执行 → title 仍空、llm 未被调用（证明非同步阻塞）
    assert not str(repo.get_research_context("p1").get("title") or "").strip()
    assert llm.calls == []
    pending = list(loop_mod._AUTONAME_TASKS)
    assert pending, "后台命名任务应已登记（持引用防 GC）"
    await asyncio.gather(*pending)
    assert repo.get_research_context("p1").get("title") == "大理宗族调研"


async def test_autoname_offloads_to_thread(env):
    """N09：阻塞的命名模型调用在 to_thread 工作线程执行，不占事件循环线程。"""
    import threading
    repo, tm = env
    repo.append_message("p1", "user", "去大理研究宗族")
    main_thread = threading.get_ident()
    seen = {}

    class ThreadProbeLLM:
        def chat(self, messages, **kw):
            seen["thread"] = threading.get_ident()
            return {"content": "线程名字"}

    from fieldwork_agent.web_host.agent import loop as loop_mod
    loop_mod._schedule_autoname(repo, "p1", ThreadProbeLLM())
    await asyncio.gather(*list(loop_mod._AUTONAME_TASKS), return_exceptions=True)
    assert seen["thread"] != main_thread   # 在工作线程执行 → 未阻塞事件循环线程
    assert repo.get_research_context("p1").get("title") == "线程名字"


def test_autoname_llm_none_falls_back_to_topic(env):
    """任务1：降级模式（llm=None）→ topic 前 30 字兑底。"""
    repo, tm = env
    repo.update_research_context("p1", {"topic": "宗族变迁"})
    from fieldwork_agent.web_host.agent.loop import _maybe_autoname
    _maybe_autoname(repo, "p1", None)
    assert repo.get_research_context("p1").get("title") == "宗族变迁"


def test_autoname_llm_none_falls_back_to_first_user_message(env):
    """任务1：降级且无 topic → 首条 user 消息去换行后前 30 字。"""
    repo, tm = env
    repo.append_message("p1", "user", "第一行\n第二行用户消息")
    from fieldwork_agent.web_host.agent.loop import _maybe_autoname
    _maybe_autoname(repo, "p1", None)
    assert repo.get_research_context("p1").get("title") == "第一行 第二行用户消息"


def test_autoname_llm_error_or_blank_silent_no_write(env):
    """任务1：LLM 报错/返回空白 → 静默返回不写不抛（best-effort，下回合再试）。"""
    repo, tm = env
    repo.append_message("p1", "user", "hi")
    from fieldwork_agent.web_host.agent.loop import _maybe_autoname
    _maybe_autoname(repo, "p1", AutonameLLM(error=RuntimeError("boom")))
    assert not repo.get_research_context("p1").get("title")
    _maybe_autoname(repo, "p1", AutonameLLM(content="\n\n"))  # 全空白
    assert not repo.get_research_context("p1").get("title")

# ------------------------------- 快捷选项标记（B 节：[[options]] 轮末抽取）


def test_options_tag_stripped_meta_and_turn_done_carry_quick_replies(env):
    """B 节：回复尾部 [[options]] 标记轮末剥离——干净正文入史、选项写入
    消息 metadata.quick_replies 与 turn_done payload（前端据此渲染 chips）。"""
    repo, tm = env
    reply = ("你想先校准哪一项？\n\n"
             "[[options]]研究问题|田野地点|时间窗[[/options]]")
    llm = FakeLLM([[
        {"delta": reply},
        {"content": reply, "tool_calls": None, "finish_reason": "stop",
         "usage": {"total_tokens": 5}},
    ]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    assistant = next(m for m in repo.get_messages("p1")
                     if m["role"] == "assistant")
    assert assistant["content"] == "你想先校准哪一项？"  # 标记与尾随空白已剥离
    assert assistant["metadata"]["quick_replies"] == ["研究问题", "田野地点", "时间窗"]
    assert assistant["metadata"]["quick_reply_groups"] == [
        {"label": "", "options": ["研究问题", "田野地点", "时间窗"]}]
    assert assistant["metadata"]["tool_calls"] == 0
    done = next(e for e in tm.replay("p1", turn.turn_id, after_seq=0)
                if e.event == "turn_done")
    assert done.data["quick_replies"] == ["研究问题", "田野地点", "时间窗"]
    assert done.data["quick_reply_groups"] == [
        {"label": "", "options": ["研究问题", "田野地点", "时间窗"]}]


def test_no_options_tag_reply_intact_and_quick_replies_empty(env):
    """无标记回合（渐进增强基准）：正文原样入史、metadata 无 quick_replies
    键、turn_done payload 携带空表。"""
    repo, tm = env
    llm = FakeLLM([[{"delta": "好的，已记录。"}, {"content": "好的，已记录。",
                    "tool_calls": None, "finish_reason": "stop", "usage": {}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    assistant = next(m for m in repo.get_messages("p1")
                     if m["role"] == "assistant")
    assert assistant["content"] == "好的，已记录。"
    assert "quick_replies" not in (assistant.get("metadata") or {})
    assert "quick_reply_groups" not in (assistant.get("metadata") or {})
    done = next(e for e in tm.replay("p1", turn.turn_id, after_seq=0)
                if e.event == "turn_done")
    assert done.data["quick_replies"] == []
    assert done.data["quick_reply_groups"] == []


def test_multi_group_options_persisted_and_emitted(env):
    """多组带标签选项（问题①）：一次问两个可枚举问题、每问一组选项，
    两组均写入 metadata.quick_reply_groups 与 turn_done；正文剥离全部标记；
    多组时旧 quick_replies 不再携带误导性子集（为空、不入史）。"""
    repo, tm = env
    reply = ("先定两件事：\n\n**1. 用途？**\n**2. 学科？**\n\n"
             "[[options:用途]]高校开题|基金申报[[/options]]\n"
             "[[options:学科]]人类学|社会学[[/options]]")
    llm = FakeLLM([[
        {"delta": reply},
        {"content": reply, "tool_calls": None, "finish_reason": "stop",
         "usage": {"total_tokens": 5}},
    ]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    assistant = next(m for m in repo.get_messages("p1")
                     if m["role"] == "assistant")
    assert "[[options" not in assistant["content"]   # 两组标记均已剥离
    assert assistant["content"].startswith("先定两件事：")
    assert assistant["metadata"]["quick_reply_groups"] == [
        {"label": "用途", "options": ["高校开题", "基金申报"]},
        {"label": "学科", "options": ["人类学", "社会学"]},
    ]
    assert "quick_replies" not in assistant["metadata"]  # 多组时旧字段为空、不入史
    done = next(e for e in tm.replay("p1", turn.turn_id, after_seq=0)
                if e.event == "turn_done")
    assert done.data["quick_replies"] == []
    assert done.data["quick_reply_groups"] == [
        {"label": "用途", "options": ["高校开题", "基金申报"]},
        {"label": "学科", "options": ["人类学", "社会学"]},
    ]


def test_extract_quick_replies_bounds():
    """抽取单元边界（多组契约）：返回 (正文, groups)，每组 {label, options}；
    每组最多 4 项、每项截 20 字；无标记/无有效选项 → groups=[] 且正文原样。"""
    from fieldwork_agent.web_host.agent.loop import _extract_quick_replies
    body, groups = _extract_quick_replies(
        "正文\n[[options]]甲|乙|丙|丁|戊[[/options]]")
    assert body == "正文"
    assert groups == [{"label": "", "options": ["甲", "乙", "丙", "丁"]}]  # 第 5 项丢弃
    long_opt = "长" * 30
    _, groups2 = _extract_quick_replies(f"[[options]]{long_opt}|乙[[/options]]")
    assert groups2 == [{"label": "", "options": ["长" * 20, "乙"]}]   # 单项截 20 字
    body3, groups3 = _extract_quick_replies("纯正文，无标记")
    assert body3 == "纯正文，无标记" and groups3 == []
    empty_tag = "[[options]] | | [[/options]]"  # 全空选项：无按钮可渲染
    body4, groups4 = _extract_quick_replies(empty_tag)
    assert groups4 == [] and body4 == empty_tag


def test_extract_quick_replies_multi_labeled_groups():
    """多组带标签：每个 [[options:标签]] 块成一组、一问一组一一对应；
    正文剥离全部块；标签截 12 字；组数上限 3。"""
    from fieldwork_agent.web_host.agent.loop import _extract_quick_replies
    body, groups = _extract_quick_replies(
        "用途？[[options:用途]]高校开题|基金申报[[/options]]"
        "学科？[[options:学科取向]]人类学|社会学[[/options]]")
    assert body == "用途？学科？"          # 两个块都被剥离
    assert groups == [
        {"label": "用途", "options": ["高校开题", "基金申报"]},
        {"label": "学科取向", "options": ["人类学", "社会学"]},
    ]
    # 标签超 12 字截断
    _, g2 = _extract_quick_replies(f"[[options:{'标'*20}]]甲[[/options]]")
    assert g2 == [{"label": "标" * 12, "options": ["甲"]}]
    # 组数上限 3：第 4 组丢弃
    four = "".join(f"[[options:g{i}]]选项{i}[[/options]]" for i in range(4))
    _, g3 = _extract_quick_replies(four)
    assert [g["label"] for g in g3] == ["g0", "g1", "g2"]
    # 全空选项的块被跳过，不产出空组
    _, g4 = _extract_quick_replies(
        "甲？[[options:a]]实|在[[/options]]乙？[[options:b]] | [[/options]]")
    assert g4 == [{"label": "a", "options": ["实", "在"]}]


# ------------------------------- 中文全角引号归一（问题3 后端）


def test_normalize_cjk_quotes_unit():
    """成对半角双引号且内容含中文 → 全角“”；纯西文内容/单边引号/代码块内
    保持原样；已全角幂等。"""
    from fieldwork_agent.web_host.agent.loop import _normalize_cjk_quotes
    assert _normalize_cjk_quotes('去"周城"看扎染') == "去“周城”看扎染"
    assert _normalize_cjk_quotes('"甲"和"乙"两个点') == "“甲”和“乙”两个点"
    assert _normalize_cjk_quotes('He said "hello" to me') == 'He said "hello" to me'
    assert _normalize_cjk_quotes('只剩"半边引号') == '只剩"半边引号'
    assert _normalize_cjk_quotes('已是“全角”的') == '已是“全角”的'
    assert _normalize_cjk_quotes(
        '代码外"归一"\n```python\nprint("中文提示")\n```\n完'
    ) == '代码外“归一”\n```python\nprint("中文提示")\n```\n完'
    # B4（终审 Parked）：行内```（非行首围栏）不参与配对——split 全文会把
    # 它当围栏使后续配对奇偶错位，把真代码段误当块外转换
    assert _normalize_cjk_quotes('用"两个"```围起来，像"这样"') \
        == "用“两个”```围起来，像“这样”"
    assert _normalize_cjk_quotes(
        '先说用```标记\n然后"这句"在围栏外\n```\nx = "中文"\n```'
    ) == '先说用```标记\n然后“这句”在围栏外\n```\nx = "中文"\n```'
    # 未闭合行首围栏：其后内容按代码块内保守处理（宁漏转正文，不误转代码）
    assert _normalize_cjk_quotes('前"话"\n```python\nx = 1') \
        == '前“话”\n```python\nx = 1'


def test_cjk_quotes_normalized_in_persisted_reply(env):
    """问题3：LLM 半角引号回复落库前归一为全角；quick_replies 抽取不受影响。"""
    repo, tm = env
    reply = ('先看"周城"的扎染坊，再访"喜洲"的民居。\n\n'
             '[[options]]先去周城|先去喜洲[[/options]]')
    llm = FakeLLM([[{"delta": reply}, {"content": reply, "tool_calls": None,
                    "finish_reason": "stop", "usage": {}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    assistant = next(m for m in repo.get_messages("p1")
                     if m["role"] == "assistant")
    assert '"周城"' not in assistant["content"]
    assert "“周城”的扎染坊，再访“喜洲”" in assistant["content"]
    assert assistant["metadata"]["quick_replies"] == ["先去周城", "先去喜洲"]


class CancelMidStreamWithOptionsLLM:
    """流式中途取消（P1-4 变体）：残缺终帧带 [[options]] 标记。"""
    def __init__(self, tm, pid, tid):
        self._tm, self._pid, self._tid = tm, pid, tid

    def chat_stream(self, messages, **kw):
        def _gen():
            yield {"delta": "部分"}
            self._tm.cancel(self._pid, self._tid)
            yield {"content": "残缺[[options]]甲|乙[[/options]]",
                   "tool_calls": None, "finish_reason": "stop", "usage": {}}
        return _gen()


def test_cancelled_turn_never_extracts_quick_replies(env):
    """取消回合不做标记抽取：带标记的残缺回复不入史、不发 turn_done，
    终态 cancelled（取消即中止本轮全部状态变更，含 quick_replies）。"""
    repo, tm = env
    turn = tm.create_turn("p1")
    llm = CancelMidStreamWithOptionsLLM(tm, "p1", turn.turn_id)
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    assert tm.get_turn("p1", turn.turn_id).status == "cancelled"
    assert [m["role"] for m in repo.get_messages("p1", limit=0)] == ["user"]
    types = [e.event for e in tm.replay("p1", turn.turn_id, after_seq=0)]
    assert "turn_done" not in types  # 无 payload 可携带选项



class RecordingGuideLLM(FakeLLM):
    """流式照脚本走；非流式 chat 记录收到的 messages 供断言。"""

    def __init__(self, script):
        super().__init__(script)
        self.chat_messages = None

    def chat(self, messages, **kw):
        self.chat_messages = messages
        return {"content": "guide"}


# ------------------------------- 截断续写防御 + finish_reason 透传
# （生产实录 field_20261018_202833 回合4：回复停在「先问最实际的：」而 turn 照常
#  completed，finish_reason 无处可查——先透传可观测，再对截断做一次续写兑底）


def test_turn_done_and_meta_carry_finish_reason(env):
    """finish_reason 可观测性：正常 stop 回合——turn_done payload 与 assistant
    metadata 均携带 finish_reason（截断排查此前无从下手）。"""
    repo, tm = env
    llm = FakeLLM([[{"delta": "好的。"}, {"content": "好的。", "tool_calls": None,
                    "finish_reason": "stop", "usage": {"total_tokens": 5}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    done = next(e for e in tm.replay("p1", turn.turn_id, after_seq=0)
                if e.event == "turn_done")
    assert done.data["finish_reason"] == "stop"
    assert done.data["incomplete"] is False
    assistant = next(m for m in repo.get_messages("p1") if m["role"] == "assistant")
    assert assistant["metadata"]["finish_reason"] == "stop"


def test_length_truncated_reply_continues_and_merges(env):
    """finish_reason=length（token 触顶截断）：追加 system 续写指令再流式一次，
    续写 deltas 与原文拼接为完整回复；只续一次、续写轮不带工具。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": "先问最实际的："},
         {"content": "先问最实际的：", "tool_calls": None,
          "finish_reason": "length", "usage": {"total_tokens": 4096}}],
        [{"delta": "你打算跟谁学艺？"},
         {"content": "你打算跟谁学艺？", "tool_calls": None,
          "finish_reason": "stop", "usage": {"total_tokens": 10}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    deltas = "".join(e.data["text"] for e in events if e.event == "message_delta")
    assert deltas == "先问最实际的：你打算跟谁学艺？"
    assert len(llm.calls) == 2
    cont_messages, cont_kw = llm.calls[1]
    assert cont_messages[-1]["role"] == "system"
    assert "截断" in cont_messages[-1]["content"]
    assert cont_kw.get("tools") is None              # 续写不带工具
    assistant = next(m for m in repo.get_messages("p1") if m["role"] == "assistant")
    assert assistant["content"] == "先问最实际的：你打算跟谁学艺？"
    done = next(e for e in events if e.event == "turn_done")
    assert done.data["finish_reason"] == "stop"      # 续写后的最终终止


class SecondStreamBrokenLLM(FakeLLM):
    """主流正常（length 截断帧）；续写流 yield 一个 delta 后异常中断。"""

    def chat_stream(self, messages, **kw):
        self.calls.append((list(messages), kw))
        if len(self.calls) == 1:
            return iter(self.script.pop(0))

        def _gen():
            yield {"delta": "已流出的续写片段"}
            raise RuntimeError("续写流中断")
        return _gen()


def test_broken_continuation_deltas_still_persisted(env):
    """B3（终审 Parked）：续写流异常中断——已发 message_delta 的文本前端已
    看到，必须兑底落库；不随 except 静默蒸发导致刷新后消失。回合照常 completed。"""
    repo, tm = env
    llm = SecondStreamBrokenLLM([
        [{"delta": "先问最实际的："},
         {"content": "先问最实际的：", "tool_calls": None,
          "finish_reason": "length", "usage": {}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    deltas = "".join(e.data["text"] for e in events if e.event == "message_delta")
    assert deltas == "先问最实际的：已流出的续写片段"
    assistant = next(m for m in repo.get_messages("p1") if m["role"] == "assistant")
    assert assistant["content"] == "先问最实际的：已流出的续写片段"
    assert tm.get_turn("p1", turn.turn_id).status == "completed"


def test_preamble_and_truncation_rules():
    from fieldwork_agent.web_host.agent.loop import _looks_like_preamble, _looks_truncated

    assert _looks_like_preamble("先问最实际的：")                       # 结尾冒号
    assert _looks_like_preamble("我这就把信息写入项目状态")              # 承诺词 + 无句号
    assert not _looks_like_preamble("这是完整回答。")
    assert not _looks_like_preamble("好的。")
    # 冒号不再算「截断」（前导语走带工具重问，而非 tools=None 续写）
    assert _looks_truncated("先问最实际的：", "stop") is False
    assert _looks_truncated("价格约 ¥65×", "stop") is True
    assert _looks_truncated("完整回答。", "length") is True
    # B2（终审 Parked）：闭合引号是完整结构、省略号是语气收尾——
    # 恰好停在它们之上的回复不是截断，误判会白打一次续写并拼接重复内容
    assert _looks_truncated('他说"就到这儿"', "stop") is False
    assert _looks_truncated("大概这些…", "stop") is False
    assert _looks_truncated("总之“先这样”", "stop") is False


def test_preamble_twice_falls_back_and_marks_incomplete(env):
    """连续两次前导语：不再重问，交收敛块；收敛仍无正文 → turn_done.incomplete=True。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": "我这就去查："},
         {"content": "我这就去查：", "tool_calls": None,
          "finish_reason": "stop", "usage": {}}],
        [{"delta": "马上开始："},
         {"content": "马上开始：", "tool_calls": None,
          "finish_reason": "stop", "usage": {}}],
        [{"content": "", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    done = next(e for e in tm.replay("p1", turn.turn_id, after_seq=0)
                if e.event == "turn_done")
    assert done.data["incomplete"] is True
    assert len(llm.calls) == 3                    # 主循环 2 次 + 收敛 1 次


def test_colon_preamble_retries_with_tools(env):
    """生产实录场景：finish_reason=stop 且回复停在「先问最实际的：」——
    这是前导语而非最终回答：带工具重问一次（不同于 length 的 tools=None 续写），
    两轮文本拼接为完整回复。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": "先问最实际的："},
         {"content": "先问最实际的：", "tool_calls": None,
          "finish_reason": "stop", "usage": {"total_tokens": 223}}],
        [{"delta": "经费来源是自费还是课题经费？"},
         {"content": "经费来源是自费还是课题经费？", "tool_calls": None,
          "finish_reason": "stop", "usage": {"total_tokens": 8}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    deltas = "".join(e.data["text"] for e in events if e.event == "message_delta")
    assert deltas == "先问最实际的：经费来源是自费还是课题经费？"
    assert len(llm.calls) == 2
    retry_messages, retry_kw = llm.calls[1]
    assert retry_kw.get("tools") is not None            # 重问必须带工具
    assert retry_messages[-1]["role"] == "system"
    assert "前导语" in retry_messages[-1]["content"]
    assistant = next(m for m in repo.get_messages("p1") if m["role"] == "assistant")
    assert assistant["content"] == "先问最实际的：经费来源是自费还是课题经费？"


def test_complete_reply_not_continued(env):
    """完整回复（句号/问号结尾 + stop）不触发续写：LLM 只调一次。"""
    repo, tm = env
    llm = FakeLLM([[{"delta": "这是完整回答。"},
                    {"content": "这是完整回答。", "tool_calls": None,
                     "finish_reason": "stop", "usage": {}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    assert len(llm.calls) == 1


# ------------------------------- [[options]] 每轮动态提醒（指令随对话变长衰减）


def test_build_messages_appends_options_reminder_after_user(env):
    """生产实录：前 2 回合规守 [[options]] 约定、后 2 回合遗忘（长对话中
    system prompt 指令衰减）。每轮 build_messages 在 user 消息后追加 system
    短提醒（对抗衰减；仅影响当轮 LLM 输入，不落库）。"""
    repo, tm = env
    from fieldwork_agent.web_host.agent.context import build_messages
    msgs = build_messages(repo, "p1", "帮我定位选题", "research_positioning")
    assert msgs[-2]["role"] == "user" and msgs[-2]["content"] == "帮我定位选题"
    assert msgs[-1]["role"] == "system"
    assert "[[options:" in msgs[-1]["content"]      # 带标签多组格式
    assert "一问一组" in msgs[-1]["content"]


def test_options_reminder_reaches_llm_every_turn(env):
    """e2e：提醒确实到达 LLM 输入（run_turn → chat_stream 的 messages 尾部）。"""
    repo, tm = env
    llm = FakeLLM([[{"delta": "好。"}, {"content": "好。", "tool_calls": None,
                    "finish_reason": "stop", "usage": {}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    msgs = llm.calls[0][0]
    assert msgs[-1]["role"] == "system" and "[[options:" in msgs[-1]["content"]
    # 提醒不落库：消息史只存 user/assistant 原文
    persisted_roles = {m["role"] for m in repo.get_messages("p1")}
    assert persisted_roles <= {"user", "assistant"}


def test_doc_tool_receives_repo_research_questions(env):
    """F01 端到端：仓储 research_questions/discipline 经 merge_doc_args 进 guide prompt。
    预设 title 隔离自动命名（任务1）——否则轮末 _maybe_autoname 会先占用
    非流式 chat 通道，覆盖 RecordingGuideLLM.chat_messages。"""
    repo, tm = env
    repo.update_research_context("p1", {
        "title": "已命名", "topic": "宗族与乡村治理",
        "research_questions": "RQ1 宗族结构", "discipline": "社会学"})
    llm = RecordingGuideLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "compose_interview_guide",
                                         "arguments": {"topic": "",
                                                       "informant_types": ["村干部"]}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "好"},
         {"content": "好", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    joined = "\n".join(m["content"] for m in llm.chat_messages)
    assert "研究主题：宗族与乡村治理" in joined
    assert "RQ1 宗族结构" in joined
    assert "学科取向：社会学" in joined


def test_doc_tool_sees_same_turn_staged_patch(env):
    """M1（终审）：同轮先 update_research_context（进 F05 暂存）再
    compose_interview_guide——入参合并必须读 buffer.staged_view() 合并视图，
    guide prompt 用本轮新值而非轮前旧值（F01×F05 组合缺口）。"""
    repo, tm = env
    repo.update_research_context("p1", {
        "title": "已命名", "topic": "仓储旧主题",
        "research_questions": "RQ 旧", "discipline": "社会学"})
    llm = RecordingGuideLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "update_research_context",
                                          "arguments": {"patch": {"topic": "本轮新主题"},
                                                        "reason": "用户确认主题"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c2", "name": "compose_interview_guide",
                                          "arguments": {"topic": "",
                                                        "informant_types": ["村干部"]}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "好"},
         {"content": "好", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "主题改了，出访谈提纲", tm,
                         llm, FakeFlyAI()))
    joined = "\n".join(m["content"] for m in llm.chat_messages)
    assert "研究主题：本轮新主题" in joined
    assert "仓储旧主题" not in joined
    # F05 语义不破：轮末 commit 落盘新值
    assert repo.get_research_context("p1")["topic"] == "本轮新主题"


# N04：同一 tool_calls 批次内写→文档的时序与正文/指纹一致性
_PROPOSAL_ARGS_EMPTY_CTX = {
    "ctx": {},   # 模型给空 ctx，由 merge_doc_args 从 staged_view 补齐
    "sites": [{"name": "大理", "confirmed": True, "profile": {"location": "大理"}}],
    "itinerary": {"legs": []},
    "informants": [{"code": "INF-01", "type": "宗族长老",
                    "relation": "同乡会引荐", "contact": "", "notes": ""}],
}


def test_n04_same_batch_write_then_proposal_uses_new_ctx(env):
    """N04：同批先 update_research_context 再 compose_proposal(ctx={})，
    文档正文必须用本轮新值（旧实现拿到轮前旧值），且落盘指纹与正文一致可确认。"""
    repo, tm = env
    repo.update_research_context("p1", {
        "discipline": "社会学", "topic": "OLD_TOPIC", "scale": "单人调研",
        "budget": "2万", "time_window": "7月", "departure_city": "上海",
        "target_location": "大理", "purpose": "global", "research_questions": "RQ_OLD"})
    llm = ChatFakeLLM([
        # 单帧双工具调用（同批）：先写主题，再出方案（ctx 空 → 由 staged_view 补）
        [{"delta": ""},
         {"content": "", "tool_calls": [
             {"id": "c1", "name": "update_research_context",
              "arguments": {"patch": {"topic": "NEW_TOPIC",
                                      "research_questions": "RQ_NEW"},
                            "reason": "用户确认"}},
             {"id": "c2", "name": "compose_proposal",
              "arguments": _PROPOSAL_ARGS_EMPTY_CTX}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "已生成"},
         {"content": "已生成", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "改主题并出方案", tm, llm, FakeFlyAI()))

    # 回合后项目主题为新值
    assert repo.get_research_context("p1")["topic"] == "NEW_TOPIC"
    # 落盘正文用新值（旧实现会含 OLD_TOPIC）
    docs = repo.list_proposals("p1")
    assert len(docs) == 1
    doc = repo.get_proposal_by_id("p1", docs[0]["id"])
    assert "NEW_TOPIC" in doc["content"]
    assert "OLD_TOPIC" not in doc["content"]
    # 依赖指纹与正文一致 → 确认可成功（正文旧/指纹新的错配已消除）
    assert repo.confirm_proposal("p1", docs[0]["id"])
    assert repo.is_proposal_confirmation_stale("p1") is False


# ------------------------------- F05 取消保护 / F06 最终回答收敛 / F07 批前截断
# （任务4：TurnPatchBuffer 回合级暂存 + has_final_answer 收敛 + 批量额度截断）


class CancelFirstChunkLLM(FakeLLM):
    """首终帧产出后置 cancel_event：模拟用户在首轮工具执行前后取消。"""

    def __init__(self, script, tm, pid, turn_id):
        super().__init__(script)
        self._tm, self._pid, self._turn = tm, pid, turn_id

    def chat_stream(self, messages, **kw):
        first = True
        for chunk in super().chat_stream(messages, **kw):
            yield chunk
            if first and chunk.get("tool_calls") is not None:
                first = False
                self._tm.get_turn(self._pid, self._turn).cancel_event.set()


_STATE_TOOL_FRAME = [{"delta": ""},
                     {"content": "", "tool_calls": [
                         {"id": "c1", "name": "update_research_context",
                          "arguments": {"patch": {"topic": "宗族与乡村治理"},
                                        "reason": "用户确认主题"}}],
                      "finish_reason": "tool_calls", "usage": {}}]
_STOP_FRAME = [{"delta": "已记录"},
               {"content": "已记录", "tool_calls": None, "finish_reason": "stop",
                "usage": {"total_tokens": 5}}]


def test_f05_cancel_discards_staged_patch(env):
    """F05：回合中途取消——状态工具写入只进暂存区，本轮不落盘，终态 cancelled。"""
    repo, tm = env
    turn = tm.create_turn("p1")
    llm = CancelFirstChunkLLM([_STATE_TOOL_FRAME, _STOP_FRAME], tm, "p1", turn.turn_id)
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "主题定为宗族与乡村治理",
                         tm, llm, FakeFlyAI()))
    assert repo.get_research_context("p1").get("topic", "") == ""   # 未提交不落盘
    assert tm.get_turn("p1", turn.turn_id).status == "cancelled"


def test_f05_normal_turn_commits_staged_patch(env):
    """F05：正常完成轮末 commit 落盘（对照组），终态 completed。"""
    repo, tm = env
    llm = FakeLLM([_STATE_TOOL_FRAME, _STOP_FRAME])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "主题定为宗族与乡村治理",
                         tm, llm, FakeFlyAI()))
    assert repo.get_research_context("p1").get("topic") == "宗族与乡村治理"
    assert tm.get_turn("p1", turn.turn_id).status == "completed"


def test_f06_preamble_budget_hit_converges_to_final_answer(env):
    """F06：前导语（"我先查一下"）+ 工具轮触顶退出——旧条件 accumulated_reply
    非空会短路收敛块；新条件 not has_final_answer 必须触发，收敛帧覆盖前导语。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": "我先查一下"},
         {"content": "我先查一下", "tool_calls": [
             {"id": "c1", "name": "search_pois", "arguments": {"city_name": "大理", "keyword": "宗祠"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "综合来看，大理有 3 处。"},
         {"content": "综合来看，大理有 3 处。", "tool_calls": None,
          "finish_reason": "stop", "usage": {"total_tokens": 9}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "大理有哪些宗祠", tm, llm,
                         FakeFlyAI(), max_tool_calls=1))
    assistant = [m for m in repo.get_messages("p1") if m["role"] == "assistant"]
    assert assistant[-1]["content"] == "综合来看，大理有 3 处。"   # 覆盖前导语
    assert tm.get_turn("p1", turn.turn_id).status == "completed"


def test_f06_convergence_empty_keeps_preamble_with_note(env):
    """F06：收敛帧仍空 → 不清前导语，走三分支的"过程说明+上限提示"分支。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": "我先查一下"},
         {"content": "我先查一下", "tool_calls": [
             {"id": "c1", "name": "search_pois", "arguments": {"city_name": "大理", "keyword": "宗祠"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"content": "", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "大理有哪些宗祠", tm, llm,
                         FakeFlyAI(), max_tool_calls=1))
    assistant = [m for m in repo.get_messages("p1") if m["role"] == "assistant"]
    assert assistant[-1]["content"].startswith("我先查一下")   # 空正文不清前导语
    assert "已达本回合处理上限" in assistant[-1]["content"]
    assert tm.get_turn("p1", turn.turn_id).status == "completed"


def test_f07_web_batch_truncated_before_dispatch(env):
    """F07：一批 2 调用只剩 1 额度 → 只执行第 1 个；第 2 个不再启动、直接回填
    budget_exceeded（两条 tool_call_id 均有 tool_completed，无悬空 id），回合 completed。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [
             {"id": "c1", "name": "search_pois", "arguments": {"city_name": "大理", "keyword": "宗祠"}},
             {"id": "c2", "name": "search_pois", "arguments": {"city_name": "大理", "keyword": "民居"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "基于已有材料的结论。"},
         {"content": "基于已有材料的结论。", "tool_calls": None,
          "finish_reason": "stop", "usage": {"total_tokens": 7}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "查两地", tm, llm,
                         FakeFlyAI(), max_tool_calls=1))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    completed = {e.data["call_id"]: e.data for e in events
                 if e.event == "tool_completed"}
    assert set(completed) == {"c1", "c2"}                 # 两条 id 均有回填（C3）
    assert completed["c1"]["ok"] is True
    assert completed["c2"]["ok"] is False
    assert completed["c2"]["error"]["code"] == "budget_exceeded"
    started = [e.data["call_id"] for e in events if e.event == "tool_started"]
    assert started == ["c1"]                              # 被截断者不再启动
    assert tm.get_turn("p1", turn.turn_id).status == "completed"


# ---------- Task 13：悬挂判定 + 统一终检门（spec B，R6/R8/R16/R18） ----------

@pytest.mark.parametrize("text,expected", [
    ("先问最实际的：", True),               # 全角冒号收尾（R6 生产实录形态）
    ("先问最实际的:", True),                # 半角冒号同样悬挂
    ("想了解哪方面？[[options:补充]]场地|联系方式[[/options]]", False),  # 有效选项块收尾
    ("想了解哪方面？[[options:补充]] [[/options]]", True),               # 空选项块收尾
    ("想了解哪方面？[[options:补充]] | | [[/options]]", True),           # 仅空白/竖线选项
    ("前文有块[[options:a]]甲|乙[[/options]]\n结尾再问：", True),        # 只看收尾：尾部冒号仍悬挂
    ("完整回答，句号收尾。", False),
    ("块后有正文[[options:q]]甲|乙[[/options]]\n以上，请选择。", False),  # 块后还有正文：不悬挂
    ("", False),                            # 空串不悬挂
])
def test_looks_dangling(text, expected):
    """R6：悬挂判定只看收尾结构——冒号收尾，或无有效选项的 options 块收尾。"""
    from fieldwork_agent.web_host.agent.loop import _looks_dangling
    assert _looks_dangling(text) is expected


def test_rule_reply_not_incomplete(env):
    """R16 回归护栏：无 LLM 规则降级文案即最终答案，终检门不得误标 incomplete。

    （现状已通过——防终检门引入回归：若实现后失败，说明规则分支漏置
    has_final_answer。文案尾部实测安全：_DEGRADED_NOTE 以"。）"收尾。）"""
    repo, tm = env
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "你好", tm, None, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    done = next(e for e in events if e.event == "turn_done")
    assert done.data["incomplete"] is not True


def test_convergence_length_truncation_marks_incomplete(env):
    """R16 回归：主循环空回复触发收敛，收敛帧自身 length 截断——
    旧代码不接管收敛帧 finish_reason 且无终检门，终态 completed 伪装完整。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": None, "finish_reason": "stop",
          "usage": {}}],                                   # 主循环空回复 → 触发收敛
        [{"delta": "开头，"},
         {"content": "开头，", "tool_calls": None, "finish_reason": "length",
          "usage": {}}],                                   # 收敛帧：逗号结尾 + length
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "帮我查大理的场地", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    done = next(e for e in events if e.event == "turn_done")
    assert done.data["incomplete"] is True


# ---------- Task 14：统一续流预算 + 悬挂补完（spec B，R2/R6/R18） ----------

def test_continuation_budget_caps_llm_calls(env):
    """R2：四类续流共用 MAX_CONTINUATIONS 预算——酷刑脚本（每帧冒号收尾）
    不得无限续流：LLM 调用总数封顶，终态 incomplete 如实上报。"""
    repo, tm = env
    frame = [{"delta": "缺："},
             {"content": "缺：", "tool_calls": None, "finish_reason": "stop",
              "usage": {}}]
    llm = FakeLLM([[dict(f) for f in frame] for _ in range(20)])   # 帧给足，防 pop 空
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import MAX_CONTINUATIONS, run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "查一下", tm, llm, FakeFlyAI()))
    assert len(llm.calls) <= 1 + MAX_CONTINUATIONS
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    done = next(e for e in events if e.event == "turn_done")
    assert done.data["incomplete"] is True
    deltas = "".join(e.data["text"] for e in events if e.event == "message_delta")
    # 主循环×2 + 收敛 + 悬挂补完各流出一次：补完确实发生过且被计数
    assert deltas.count("缺：") == 4


def test_dangling_completion_appends_options(env):
    """R6：收敛产出冒号收尾的悬挂回复 → 悬挂补完追加完整结尾（含选项块）；
    已流出正文全部保留；轮末剥离出 quick_reply_groups；incomplete=False。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": "我先查场地："},
         {"content": "我先查场地：", "tool_calls": None,
          "finish_reason": "stop", "usage": {}}],           # 前导语 → 带工具重问
        [{"delta": "仍缺两项："},
         {"content": "仍缺两项：", "tool_calls": None,
          "finish_reason": "stop", "usage": {}}],           # 又冒号收尾，不再重问 → break
        [{"delta": "仍缺两项："},
         {"content": "仍缺两项：", "tool_calls": None,
          "finish_reason": "stop", "usage": {}}],           # 收敛：产出仍冒号收尾
        [{"delta": "补充如下。[[options:补充]]场地|联系方式[[/options]]"},
         {"content": "补充如下。[[options:补充]]场地|联系方式[[/options]]",
          "tool_calls": None, "finish_reason": "stop", "usage": {}}],  # 悬挂补完
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "缺什么", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    done = next(e for e in events if e.event == "turn_done")
    assert done.data["incomplete"] is False
    groups = done.data["quick_reply_groups"]
    assert groups and groups[0]["options"] == ["场地", "联系方式"]
    deltas = "".join(e.data["text"] for e in events if e.event == "message_delta")
    assert "我先查场地：" in deltas and "补充如下。" in deltas   # 已流出正文绝不丢


def test_recovery_failed_cleared_after_success(env):
    """R18：recovery_failed 是「最近一次恢复」语义——截断续流失败置位后，
    悬挂补完成功清除，终检门不因早期失败误标 incomplete；失败续流已流出
    的增量照样保留。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": "内容，"},
         {"content": "内容，", "tool_calls": None, "finish_reason": "length",
          "usage": {}}],                                    # 截断 → 续写触发
        [{"delta": "清单如下："}],                           # 续写流无终帧 → recovery_failed=True
        [{"delta": "三点。"},
         {"content": "三点。", "tool_calls": None, "finish_reason": "stop",
          "usage": {}}],                                    # 悬挂补完成功 → 清除
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "给我清单", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    done = next(e for e in events if e.event == "turn_done")
    assert done.data["incomplete"] is False
    deltas = "".join(e.data["text"] for e in events if e.event == "message_delta")
    assert "清单如下：" in deltas and "三点。" in deltas      # 失败续流的增量不丢


def test_completion_frame_truncated_marks_incomplete(env):
    """R8：悬挂补完帧自身 finish_reason=length、停在「地点和」（非冒号）——
    补完块一次性不再回补，终检门 _looks_truncated 兜住 → incomplete=True。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": "我先查场地："},
         {"content": "我先查场地：", "tool_calls": None,
          "finish_reason": "stop", "usage": {}}],           # 前导语 → 带工具重问
        [{"delta": "仍缺两项："},
         {"content": "仍缺两项：", "tool_calls": None,
          "finish_reason": "stop", "usage": {}}],           # 又冒号收尾，不再重问 → break
        [{"delta": "仍缺两项："},
         {"content": "仍缺两项：", "tool_calls": None,
          "finish_reason": "stop", "usage": {}}],           # 收敛：产出仍冒号收尾
        [{"delta": "推荐三个地点和"},
         {"content": "推荐三个地点和", "tool_calls": None,
          "finish_reason": "length", "usage": {}}],          # 悬挂补完被 length 截断
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "缺什么", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    done = next(e for e in events if e.event == "turn_done")
    assert done.data["incomplete"] is True   # 终检门 _looks_truncated 捕获（R8）
    deltas = "".join(e.data["text"] for e in events if e.event == "message_delta")
    assert "推荐三个地点和" in deltas   # 已流出内容不丢


def test_dangling_completion_dead_ends_incomplete(env):
    """R18 后半：悬挂补完流无终帧且预算内无后续恢复 → recovery_failed 终值
    True → 终检门 incomplete=True；两次续流已流出的增量都保留。"""
    repo, tm = env
    llm = FakeLLM([
        [{"delta": "内容，"},
         {"content": "内容，", "tool_calls": None, "finish_reason": "length",
          "usage": {}}],                                    # 截断 → 续写触发
        [{"delta": "清单如下："}],                           # 续写流无终帧 → recovery_failed=True
        [{"delta": "甲乙丙。"}],                             # 悬挂补完同样无终帧 → 保持 True
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "给我清单", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    done = next(e for e in events if e.event == "turn_done")
    assert done.data["incomplete"] is True
    deltas = "".join(e.data["text"] for e in events if e.event == "message_delta")
    assert "清单如下：" in deltas and "甲乙丙。" in deltas   # 已流出内容不丢


def test_completion_cancel_during_hangfix_keeps_cancelled(env):
    """终审 W1（P1-4/F05）：悬挂补完流的 await 窗口内用户取消——
    TurnManager.cancel 已立即 finish("cancelled") 置终态，run_turn 跑完
    补完流后必须刷新取消快照：轮末不落 assistant 消息、不 commit 暂存、
    不发 turn_done，终态保持 cancelled（不被改写 completed）。"""
    repo, tm = env
    turn = tm.create_turn("p1")
    handle = tm.get_turn("p1", turn.turn_id)

    def _hangfix_gen():
        yield {"delta": "补充如下。"}
        # 首个 delta 已被消费（await 窗口打开）——此刻模拟 TurnManager.cancel
        # 的完整行为：cancel_event.set() + finish("cancelled") 置终态
        handle.cancel_event.set()
        tm.finish("p1", turn.turn_id, "cancelled")
        yield {"content": "补充如下。完整收尾。", "tool_calls": None,
               "finish_reason": "stop", "usage": {}}

    llm = FakeLLM([
        [{"delta": "我先查场地："},
         {"content": "我先查场地：", "tool_calls": None,
          "finish_reason": "stop", "usage": {}}],           # 前导语 → 带工具重问
        [{"delta": "仍缺两项："},
         {"content": "仍缺两项：", "tool_calls": None,
          "finish_reason": "stop", "usage": {}}],           # 又冒号收尾，不再重问 → break
        [{"delta": "仍缺两项："},
         {"content": "仍缺两项：", "tool_calls": None,
          "finish_reason": "stop", "usage": {}}],           # 收敛：产出仍冒号收尾
        _hangfix_gen(),                                     # 悬挂补完：中途取消
    ])
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "缺什么", tm, llm, FakeFlyAI()))
    # 终态 cancelled 未被改写（轮末二次 finish("cancelled") 幂等无害）
    assert tm.get_turn("p1", turn.turn_id).status == "cancelled"
    # P1-4：取消即中止本轮全部状态变更——无 assistant 消息 append
    assert [m["role"] for m in repo.get_messages("p1", limit=0)] == ["user"]
    # 无 turn_done（cancelled 终态不发完成事件）
    types = [e.event for e in tm.replay("p1", turn.turn_id, after_seq=0)]
    assert "turn_done" not in types


# ───────────────────── 内容安全：输入拦截（哑词注入，不依赖真实词条） ─────────────

def test_run_turn_input_blocked_rejects_without_llm(env, monkeypatch):
    """命中输入侧判定：固定拒答、llm 零调用、assistant 落库为拒答文案。"""
    from fieldwork_agent.kernel.guidance import safety as safety_mod
    from fieldwork_agent.kernel.guidance.safety import INPUT_REFUSAL
    from fieldwork_agent.web_host.agent.loop import run_turn

    # 词表参数化的宿主层等价形态：monkeypatch 换入哑词表，走真实判定函数
    monkeypatch.setattr(safety_mod, "_INPUT_RE",
                        safety_mod._combo_pattern(("弄到",), ("哑物甲",)))
    repo, tm = env
    turn = tm.create_turn("p1")
    llm = FakeLLM([])          # 脚本为空：被调用即 IndexError 暴露
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "帮我弄到哑物甲，急着用",
                         tm, llm, FakeFlyAI()))
    assert llm.calls == []     # 不消耗模型调用
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    sb = [e for e in events if e.event == "safety_blocked"]
    assert sb and sb[0].data["side"] == "input"
    done = [e for e in events if e.event == "turn_done"][0]
    assert done.data["safety_blocked"] is True
    deltas = "".join(e.data["text"] for e in events if e.event == "message_delta")
    assert deltas == INPUT_REFUSAL
    msgs = repo.get_messages("p1")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[-1]["content"] == INPUT_REFUSAL
    assert msgs[-1]["metadata"].get("safety_blocked") is True
    assert tm.get_turn("p1", turn.turn_id).status == "completed"


def test_run_turn_normal_text_unaffected(env):
    """正常研究对话不受输入拦截影响（回归锚：默认词表零误伤）。"""
    from fieldwork_agent.web_host.agent.loop import run_turn

    repo, tm = env
    turn = tm.create_turn("p1")
    llm = FakeLLM([[{"delta": "好的"},
                    {"content": "已收到", "tool_calls": None,
                     "finish_reason": "stop", "usage": {}}]])
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "帮我规划大理的田野行程",
                         tm, llm, FakeFlyAI()))
    assert len(llm.calls) == 1
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    assert "safety_blocked" not in [e.event for e in events]


# ───────────────────── 内容安全：输出终检（哑词注入，不依赖真实词条） ────────

def test_run_turn_output_blocked_replaces_persisted_reply(env, monkeypatch):
    """模型回复命中输出窄表：落库/turn_done 载体替换为拦截文案，选项清空。"""
    from fieldwork_agent.kernel.guidance import safety as safety_mod
    from fieldwork_agent.kernel.guidance.safety import SAFETY_BLOCK_NOTICE
    from fieldwork_agent.web_host.agent.loop import run_turn

    monkeypatch.setattr(safety_mod, "_OUTPUT_RE",
                        safety_mod._combo_pattern(("教程",), ("哑物丙",)))
    repo, tm = env
    turn = tm.create_turn("p1")
    llm = FakeLLM([[{"delta": "以下是"},
                    {"content": "哑物丙教程的第一步是准备材料。",
                     "tool_calls": None,
                     "finish_reason": "stop", "usage": {}}]])
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "随便问我什么",
                         tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    sb = [e for e in events if e.event == "safety_blocked"]
    assert sb and sb[0].data["side"] == "output"
    done = [e for e in events if e.event == "turn_done"][0]
    assert done.data["safety_blocked"] is True
    assert done.data["quick_reply_groups"] == []
    msgs = repo.get_messages("p1")
    assert msgs[-1]["content"] == SAFETY_BLOCK_NOTICE   # 原文不落库
    assert msgs[-1]["metadata"].get("safety_blocked") is True


def test_run_turn_clean_reply_unaffected(env):
    """正常回复不经替换（回归锚）。"""
    from fieldwork_agent.web_host.agent.loop import run_turn

    repo, tm = env
    turn = tm.create_turn("p1")
    llm = FakeLLM([[{"content": "大理的田野行程建议：先抵昆明再转大理，"
                                "田野点集中在古城周边。",
                     "tool_calls": None, "finish_reason": "stop", "usage": {}}]])
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "规划大理行程",
                         tm, llm, FakeFlyAI()))
    msgs = repo.get_messages("p1")
    assert msgs[-1]["content"].startswith("大理的田野行程")
    assert "safety_blocked" not in msgs[-1]["metadata"]


# ───────────────────── 内容安全：平台审核错误与 Key 错误分流 ─────────────────

def test_run_turn_moderation_error_distinct_code(env):
    """平台审核错误 → code=content_moderation，hint 不再误指 API Key。"""
    from fieldwork_agent.web_host.agent.loop import run_turn

    repo, tm = env
    turn = tm.create_turn("p1")
    llm = FakeLLM([[{"content": "", "tool_calls": None, "finish_reason": "",
                     "usage": {},
                     "error": "StatusCode: 400, Code: data_inspection_failed, "
                              "Message: inappropriate content detected"}]])
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "触发审核的文本", tm, llm, None))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    err = [e for e in events if e.event == "error"][0]
    assert err.data["code"] == "content_moderation"
    assert "Key" not in err.data["hint"]


def test_run_turn_key_error_keeps_old_hint(env):
    """普通 key 错误维持既有 llm_error + Key hint（回归锚）。"""
    from fieldwork_agent.web_host.agent.loop import run_turn

    repo, tm = env
    turn = tm.create_turn("p1")
    llm = FakeLLM([[{"content": "", "tool_calls": None, "finish_reason": "",
                     "usage": {}, "error": "Invalid API-key provided"}]])
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, None))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    err = [e for e in events if e.event == "error"][0]
    assert err.data["code"] == "llm_error"
    assert "Key" in err.data["hint"]


def test_proposal_confirm_hash_follows_merged_ctx(env):
    """C01：方案指纹忠于正文实际所本（合并后 ctx），而非仓储旧值。

    仓储 budget=2万、模型 compose_proposal 入参主张 budget=3万时，正文按 3万
    渲染；生成即确认必须被拒（文档所本与仓储不一致），把仓储同步为 3万后通过。
    """
    repo, tm = env
    args = json.loads(json.dumps(_PROPOSAL_ARGS))   # deep copy
    args["ctx"]["budget"] = "3万"
    # 仓储先全量置为模型 ctx，再改回 2万——只留 budget 一路差异
    repo.update_research_context("p1", args["ctx"])
    repo.update_research_context("p1", {"budget": "2万"})
    llm = ChatFakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "compose_proposal",
                                          "arguments": args}],
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
    # M-1：正文按模型主张的 3万 渲染（模板降级路径 budget 字面值），
    # 指纹跟随正文所本——正文与仓储不一致才是拒绝的真正原因
    assert "3万" in docs[0]["content"]
    from fieldwork_agent.kernel.repository.confirm_hash import ProposalConfirmError
    with pytest.raises(ProposalConfirmError) as ei:
        repo.confirm_proposal("p1", docs[0]["id"])
    assert ei.value.code == "dep_hash_mismatch"
    # 仓储同步为文档实际所本 → 确认通过
    repo.update_research_context("p1", {"budget": "3万"})
    repo.confirm_proposal("p1", docs[0]["id"])
    assert repo.is_proposal_confirmation_stale("p1") is False
