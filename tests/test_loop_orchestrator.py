"""runtime/loop.py 共享循环核心 + FieldAgent 内核宿主接线（Task 9）。

R5 修订：loop 放 ``fieldwork_agent.runtime.loop``（非 kernel）。测试全 mock：
ScriptedLLM 脚本驱动、dispatch 用桩，不联网、不调 subprocess、无 key。
"""
from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.runtime.loop import LoopResult, run_tool_loop

# 8 搜索原语名集（与 kernel.sources.flyai 注册一致；import 即触发 register）
_SEARCH_NAMES = {
    "search_flights", "search_trains", "search_hotels", "search_marriott_hotels",
    "search_marriott_packages", "search_pois", "search_keyword", "search_ai",
}


class ScriptedLLM:
    """按脚本顺序返回 chat 响应；记录每次收到的 messages/kw 供断言。"""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def chat(self, messages, **kw):
        self.seen.append((list(messages), kw))
        return self.script.pop(0)


# ---------------------------------------------------------------- loop 核心


def test_two_tool_rounds_then_answer():
    calls = []

    def dispatch(name, args):
        calls.append(name)
        return Envelope(ok=True, data={"items": []})

    llm = ScriptedLLM([
        {"content": "", "tool_calls": [
            {"id": "1", "name": "search_hotels", "arguments": {"dest_name": "大理"}}],
         "finish_reason": "tool_calls"},
        {"content": "", "tool_calls": [
            {"id": "2", "name": "search_pois", "arguments": {"city_name": "大理"}}],
         "finish_reason": "tool_calls"},
        {"content": "回答完毕", "tool_calls": None, "finish_reason": "stop"},
    ])
    r = run_tool_loop(llm, [{"role": "user", "content": "hi"}], dispatch, tools=[])
    assert isinstance(r, LoopResult)
    assert r.reply == "回答完毕"
    assert calls == ["search_hotels", "search_pois"]
    assert r.iterations == 3
    assert r.stopped_by_limit is False
    assert len(r.tool_events) == 2
    assert {e["tool"] for e in r.tool_events} == {"search_hotels", "search_pois"}


def test_iteration_cap():
    tc = {"content": "", "tool_calls": [
        {"id": "x", "name": "search_ai", "arguments": {"query": "q"}}],
        "finish_reason": "tool_calls"}
    llm = ScriptedLLM([tc] * 12)
    dispatch_calls = []

    def dispatch(name, args):
        dispatch_calls.append(name)
        return Envelope(ok=True, data={})

    r = run_tool_loop(llm, [{"role": "user", "content": "hi"}], dispatch, tools=[],
                      max_iters=8)
    assert r.stopped_by_limit, "超过8迭代必须停止(spec §3.3)"
    # 8 轮迭代各派发一次；触顶后追加 system 提示再取一次最终回答
    assert len(dispatch_calls) == 8
    assert r.iterations == 9


def test_tool_error_envelope_goes_back_as_tool_message():
    events = []

    def dispatch(name, args):
        return Envelope(ok=False,
                        error=ToolError(code="flyai_unavailable", message="no cli",
                                        hint="装 CLI"))

    def on_event(ev, payload):
        events.append((ev, payload))

    llm = ScriptedLLM([
        {"content": "", "tool_calls": [
            {"id": "1", "name": "search_ai", "arguments": {}}], "finish_reason": "tool_calls"},
        {"content": "CLI不可用", "tool_calls": None},
    ])
    r = run_tool_loop(llm, [{"role": "user", "content": "x"}], dispatch, tools=[],
                      on_event=on_event)
    assert r.reply == "CLI不可用"
    started = [p for ev, p in events if ev == "tool_started"]
    completed = [p for ev, p in events if ev == "tool_completed"]
    assert started, "on_event 应收到 tool_started"
    assert completed and completed[0]["ok"] is False, "on_event 应收到 tool_completed(ok=False)"
    # 错误包络以摘要回填 tool 消息（结构化摘要，非整段倾倒）
    tool_msgs = [m for m in llm.seen[-1][0] if m.get("role") == "tool"]
    assert tool_msgs and '"ok": false' in tool_msgs[-1]["content"]


def test_tool_call_cap_stops():
    tc = {"content": "", "tool_calls": [
        {"id": "x", "name": "search_ai", "arguments": {"query": "q"}}],
        "finish_reason": "tool_calls"}
    llm = ScriptedLLM([tc] * 12)

    def dispatch(name, args):
        return Envelope(ok=True, data={})

    r = run_tool_loop(llm, [{"role": "user", "content": "hi"}], dispatch, tools=[],
                      max_iters=8, max_tool_calls=1)
    assert r.stopped_by_limit is True
    assert len(r.tool_events) == 1, "总调用数上限=1 时只派发一次"


# ---------------------------------------------------------------- orchestrator 接线


def test_orchestrator_chat_persists_context_and_no_premature_advance(fake_config, fake_llm):
    """FakeLLM 脚本驱动 chat：extract 落盘 target_location，且研究定位阶段不误推进。"""
    from fieldwork_agent.core.orchestrator import FieldAgent

    agent = FieldAgent(fake_config)
    agent.create_project("p_bai")
    agent.llm = fake_llm([{"content": "已记录：目标地点大理。", "tool_calls": None,
                           "finish_reason": "stop"}])

    out = agent.chat("我想去大理调研白族，单人三个月预算2万")

    # 返回键严格为 {reply, phase, tool_events, phase_transition}
    assert set(out) == {"reply", "phase", "tool_events", "phase_transition"}
    assert out["reply"] == "已记录：目标地点大理。"
    ctx = agent.repo.get_research_context("p_bai")
    assert ctx["target_location"] == "大理"
    assert ctx["scale"] == "单人调研"
    # 可提取信息不足 4 项，check_advance 确定性判定不应推进（防幻觉推进）
    assert out["phase_transition"] is None
    assert out["phase"] == "research_positioning"
    assert out["tool_events"] == []


def test_orchestrator_dispatch_unknown_and_invalid(fake_config):
    """dispatch 统一兜底：未知工具→unknown_tool；参数校验失败→invalid_arguments。"""
    from fieldwork_agent.core.orchestrator import FieldAgent

    agent = FieldAgent(fake_config)

    unknown = agent.client_kernel_dispatch("no_such_tool", {})
    assert unknown.ok is False and unknown.error.code == "unknown_tool"

    # profile_field_site 缺必填 location → invalid_arguments（不进 handler，不裸抛）
    bad = agent.client_kernel_dispatch("profile_field_site", {})
    assert bad.ok is False and bad.error.code == "invalid_arguments"


def test_orchestrator_dispatch_search_stub_injects_flyai(fake_config, fake_flyai):
    """搜索类经 registry args_model 校验后注入 flyai；成功 data 供轮末落盘。

    registry 校验会把 args_model 全部字段（含默认值）透传给 client，
    故断言方法名与关键入参（dest_name），而非仅原始两键 dict。
    """
    from fieldwork_agent.core.orchestrator import FieldAgent

    agent = FieldAgent(fake_config)
    agent.flyai = fake_flyai

    env = agent.client_kernel_dispatch("search_hotels", {"dest_name": "大理"})
    assert env.ok is True
    name, kwargs = fake_flyai.calls[0]
    assert name == "search_hotels"
    assert kwargs["dest_name"] == "大理"


def test_orchestrator_profile_event_persists_field_site(fake_config, fake_flyai, fake_llm):
    """profile_field_site 成功后轮末落盘为 field_site（未确认），供后续 B→C 判定。"""
    from fieldwork_agent.core.orchestrator import FieldAgent

    agent = FieldAgent(fake_config)
    agent.create_project("p_site")
    agent.flyai = fake_flyai
    agent.llm = fake_llm([
        {"content": "", "tool_calls": [
            {"id": "1", "name": "profile_field_site",
             "arguments": {"location": "大理"}}], "finish_reason": "tool_calls"},
        {"content": "已生成大理画像。", "tool_calls": None, "finish_reason": "stop"},
    ])

    out = agent.chat("帮我看下大理")
    assert out["reply"] == "已生成大理画像。"
    assert len(out["tool_events"]) == 1
    assert out["tool_events"][0]["tool"] == "profile_field_site"
    assert out["tool_events"][0]["ok"] is True
    sites = agent.repo.list_field_sites("p_site")
    assert any(s["name"] == "大理" for s in sites)


# ------------------------------------------------- 搜索原语单一来源（Task 9 修复）


def test_search_primitives_all_registered():
    """8 搜索原语进 registry：名字集合可从 build_tool_schemas 全见（单一来源）。"""
    import fieldwork_agent.kernel.sources.flyai  # noqa: F401 — 触发 register
    from fieldwork_agent.kernel import build_tool_schemas

    names = {t["function"]["name"] for t in build_tool_schemas()}
    assert _SEARCH_NAMES <= names, f"缺失搜索原语注册: {_SEARCH_NAMES - names}"
    # 抽一条校验 schema 形状：search_hotels 带 dest_name 必填、client 注入参数不进 schema
    hotel = [t for t in build_tool_schemas() if t["function"]["name"] == "search_hotels"][0]
    params = hotel["function"]["parameters"]
    assert "dest_name" in params["properties"]
    assert "dest_name" in params["required"]
    assert "client" not in params["properties"]


def test_orchestrator_dispatch_search_invalid_arguments(fake_config):
    """dispatch 对搜索原语走 registry args 校验：缺必填 dest_name → invalid_arguments。"""
    from fieldwork_agent.core.orchestrator import FieldAgent

    agent = FieldAgent(fake_config)
    # 未注入 flyai 也无妨：校验在调用前失败，不触达 client
    env = agent.client_kernel_dispatch("search_hotels", {})
    assert env.ok is False and env.error.code == "invalid_arguments"


def test_orchestrator_no_llm_rule_fallback(fake_config):
    """无 LLM（FakeConfig 无 key、agent.llm=None）降级：chat 返回严格四键且 reply 非空。"""
    from fieldwork_agent.core.orchestrator import FieldAgent

    agent = FieldAgent(fake_config)
    assert agent.llm is None  # FakeConfig.dashscope_available=False → 不构造 LLM
    agent.create_project("p_nokey")

    out = agent.chat("我想去大理做白族田野调查")
    assert set(out) == {"reply", "phase", "tool_events", "phase_transition"}
    assert out["reply"]  # 规则文案非空
    assert out["tool_events"] == []
    assert out["phase"] == "research_positioning"
