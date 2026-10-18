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
                              0, label="amap")
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
    _drain_snapshots(repo, "p1", rec, snapshot_ids, live_refs, cursor, label="amap")
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
    assert assistant["metadata"]["tool_calls"] == 0
    done = next(e for e in tm.replay("p1", turn.turn_id, after_seq=0)
                if e.event == "turn_done")
    assert done.data["quick_replies"] == ["研究问题", "田野地点", "时间窗"]


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
    done = next(e for e in tm.replay("p1", turn.turn_id, after_seq=0)
                if e.event == "turn_done")
    assert done.data["quick_replies"] == []


def test_extract_quick_replies_bounds():
    """抽取单元边界：最多 4 项、每项截 20 字；解析不出有效选项回落原文。"""
    from fieldwork_agent.web_host.agent.loop import _extract_quick_replies
    body, opts = _extract_quick_replies(
        "正文\n[[options]]甲|乙|丙|丁|戊[[/options]]")
    assert body == "正文"
    assert opts == ["甲", "乙", "丙", "丁"]  # 第 5 项丢弃
    long_opt = "长" * 30
    _, opts2 = _extract_quick_replies(f"[[options]]{long_opt}|乙[[/options]]")
    assert opts2 == ["长" * 20, "乙"]          # 单项截 20 字
    body3, opts3 = _extract_quick_replies("纯正文，无标记")
    assert body3 == "纯正文，无标记" and opts3 == []
    empty_tag = "[[options]] | | [[/options]]"  # 全空选项：无按钮可渲染
    body4, opts4 = _extract_quick_replies(empty_tag)
    assert opts4 == [] and body4 == empty_tag


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


def test_half_sentence_stop_triggers_continuation(env):
    """生产实录场景：finish_reason=stop 但回复停在「先问最实际的：」——
    服务端异常终止。非空回复以未完标点（：，、；—…及开引号）结尾同样
    触发一次续写。"""
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
    deltas = "".join(e.data["text"] for e in tm.replay("p1", turn.turn_id, after_seq=0)
                     if e.event == "message_delta")
    assert deltas == "先问最实际的：经费来源是自费还是课题经费？"
    assert len(llm.calls) == 2


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
    assert "[[options]]" in msgs[-1]["content"]


def test_options_reminder_reaches_llm_every_turn(env):
    """e2e：提醒确实到达 LLM 输入（run_turn → chat_stream 的 messages 尾部）。"""
    repo, tm = env
    llm = FakeLLM([[{"delta": "好。"}, {"content": "好。", "tool_calls": None,
                    "finish_reason": "stop", "usage": {}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    msgs = llm.calls[0][0]
    assert msgs[-1]["role"] == "system" and "[[options]]" in msgs[-1]["content"]
    # 提醒不落库：消息史只存 user/assistant 原文
    persisted_roles = {m["role"] for m in repo.get_messages("p1")}
    assert persisted_roles <= {"user", "assistant"}


def test_doc_tool_receives_repo_research_questions(env):
    """F01 端到端：仓储 research_questions/discipline 经 merge_doc_args 进 guide prompt。"""
    repo, tm = env
    repo.update_research_context("p1", {
        "topic": "宗族与乡村治理", "research_questions": "RQ1 宗族结构",
        "discipline": "社会学"})
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
