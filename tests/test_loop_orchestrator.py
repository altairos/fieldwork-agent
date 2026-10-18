"""runtime/loop.py 共享循环核心 + FieldAgent 内核宿主接线（Task 9）。

R5 修订：loop 放 ``fieldwork_agent.runtime.loop``（非 kernel）。测试全 mock：
ScriptedLLM 脚本驱动、dispatch 用桩，不联网、不调 subprocess、无 key。
"""
from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.runtime.loop import LoopResult, _summarize, _tool_content, run_tool_loop

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


def test_rate_limit_error_transparent_in_event_and_summary():
    """flyai_rate_limited 配额错误：completed 事件与 tool 摘要均携带 error 三元组。

    前端靠事件里的 error.code 驱动配额提示卡；LLM 靠摘要里的 hint 转述申请地址
    （其他端：CLI/MCP 经同一摘要链路呈现链接）。
    """
    events = []

    def dispatch(name, args):
        return Envelope(ok=False,
                        error=ToolError(code="flyai_rate_limited",
                                        message="rate limited",
                                        hint="飞猪 API 配额说明：申请 https://flyai.open.fliggy.com/"))

    def on_event(ev, payload):
        events.append((ev, payload))

    llm = ScriptedLLM([
        {"content": "", "tool_calls": [
            {"id": "1", "name": "search_ai", "arguments": {}}], "finish_reason": "tool_calls"},
        {"content": "配额用尽", "tool_calls": None},
    ])
    run_tool_loop(llm, [{"role": "user", "content": "x"}], dispatch, tools=[],
                  on_event=on_event)
    completed = [p for ev, p in events if ev == "tool_completed"][0]
    assert completed["error"]["code"] == "flyai_rate_limited"
    assert "flyai.open.fliggy.com" in completed["error"]["hint"]
    # 摘要同样带 hint，LLM 可转述申请地址
    tool_msgs = [m for m in llm.seen[-1][0] if m.get("role") == "tool"]
    assert tool_msgs and "flyai.open.fliggy.com" in tool_msgs[-1]["content"]


def test_success_completed_event_error_is_none():
    """成功事件 error 为 None（additive 字段，不污染既有消费者）。"""
    events = []

    def dispatch(name, args):
        return Envelope(ok=True, data={"items": []})

    def on_event(ev, payload):
        events.append((ev, payload))

    llm = ScriptedLLM([
        {"content": "", "tool_calls": [
            {"id": "1", "name": "search_ai", "arguments": {}}], "finish_reason": "tool_calls"},
        {"content": "done", "tool_calls": None},
    ])
    run_tool_loop(llm, [{"role": "user", "content": "x"}], dispatch, tools=[],
                  on_event=on_event)
    completed = [p for ev, p in events if ev == "tool_completed"][0]
    assert completed["error"] is None


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


def test_orchestrator_advance_finished_requires_confirmed_proposal(fake_config):
    """P2-6：CLI 轮末推进 D→finished 须传 proposal_confirmed，与 web 宿主对齐。

    仅有草稿未确认 → 不推进；确认后（proposal_confirmed 且非 stale）才推进。
    """
    from fieldwork_agent.core.orchestrator import FieldAgent

    agent = FieldAgent(fake_config)
    agent.create_project("p_conf")
    repo = agent.repo
    repo.set_phase("p_conf", "proposal_generation")

    # 仅有草稿、未确认 → 不推进（missing proposal_confirmation）
    repo.save_proposal("p_conf", "# 方案", kind="proposal", version="final")
    assert agent._advance("p_conf") is None

    # 确认后（dep_hash 一致非 stale）→ 推进 finished
    repo.confirm_proposal("p_conf")
    assert agent._advance("p_conf") == {"from": "proposal_generation", "to": "finished"}


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


# ------------------------------------- dispatch 完备性（final-review Fix-1 钉子）


def test_capability_dispatch_covers_all_registered_tools():
    """钉子：_CAPABILITY_DISPATCH 键集与 registry 能力面全等。

    ``_build_tools`` 广告 build_tool_schemas() 全部能力，dispatch 表缺键会让
    CLI 会话中 LLM 的调用落到 unknown_tool（终审 Major-1 的缺口形态）。
    """
    from fieldwork_agent.core.orchestrator import _CAPABILITY_DISPATCH
    from fieldwork_agent.kernel.registry import capabilities

    assert set(_CAPABILITY_DISPATCH) == set(capabilities())


def test_orchestrator_dispatch_analyze_window_combo_injection(fake_config, fake_flyai,
                                                               fake_llm):
    """analyze_fieldwork_window 需 client+llm 双注入：修复前不在 dispatch 表（unknown_tool），
    钉住组合注入 ``("client", "llm")`` 派发成功。"""
    from fieldwork_agent.core.orchestrator import FieldAgent

    agent = FieldAgent(fake_config)
    agent.flyai = fake_flyai
    agent.llm = fake_llm([{"content": "[]", "tool_calls": None, "finish_reason": "stop"}])

    env = agent.client_kernel_dispatch("analyze_fieldwork_window", {
        "departure": "昆明", "destination": "西昌",
        "range_start": "2026-07-01", "range_end": "2026-07-15",
        "duration_days": 3, "budget_total": 5000.0, "sample_step": 7})
    assert env.ok is True, getattr(env, "error", None)


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


# ------------------------------------- 验收 2：无 key 全链路 new→chat→status→generate


def test_acceptance_smoke_no_key(fake_config):
    """验收2 全链路（无 key）：新建项目 → chat 输入研究描述 → status 采集字段与阶段 → generate 八章。

    本条同时补上 ``FieldAgent.generate_proposal`` 的宿主接线验证（此前零覆盖）。
    全程 FakeConfig 无 dashscope/flyai key、不联网：
    - chat 走 ``_rule_based_reply`` 降级文案，extract 仍确定性落采集字段；
    - compose_proposal 的四硬渠道无 key 时拿不到真实 flyai 数据，故按宿主
      ``_persist_events`` 的落盘形状预置（save_field_site / cache_search(compose_itinerary)
      / save_informant）——等价于真实工具轮写进 repo 的内容；
    - generate 走 ``llm=None`` 模板降级分支：严格 8 个 ``## `` 章并 save_proposal 落盘可见。
    """
    from pathlib import Path

    from fieldwork_agent.core.orchestrator import FieldAgent
    from fieldwork_agent.kernel.documents.proposal import PROPOSAL_SECTIONS
    from fieldwork_agent.kernel.guidance.phases import (
        PHASE_RESEARCH_POSITIONING,
        PHASES,
        REQUIRED_FIELDS,
    )
    from fieldwork_agent.kernel.models import ResearchContext

    agent = FieldAgent(fake_config)
    assert agent.llm is None and not fake_config.dashscope_available, "无 key 前提"
    pid = agent.create_project("accept2")
    assert agent.project_id == pid

    # ① 研究描述 → 规则降级回复（四键契约不破）
    out = agent.chat("我是人类学初学者，想去大理调研白族宗族，单人三个月预算2万")
    assert set(out) == {"reply", "phase", "tool_events", "phase_transition"}
    assert out["reply"].strip(), "无 key 必须给出降级文案而非空回复"
    assert out["tool_events"] == []

    # ② status 数据面：采集字段落 research_context、阶段合法且不足 4 项不冒进
    state = agent.repo.get_state(pid)
    ctx = state["research_context"]
    assert ctx["target_location"] == "大理"
    assert ctx["discipline"] == "人类学"
    assert ctx["scale"] == "单人调研"
    filled = {k for k, v in ctx.items() if k != "extras" and str(v).strip()}
    assert filled <= set(ResearchContext.model_fields), "采集项必须是研究上下文槽位"
    assert filled & set(REQUIRED_FIELDS[PHASE_RESEARCH_POSITIONING]) == {"discipline", "scale", "budget"}
    assert state["phase"] in PHASES
    assert out["phase"] == state["phase"] == PHASE_RESEARCH_POSITIONING
    assert out["phase_transition"] is None

    # 渠道未齐时 generate 折成 {ok:False, error}（宿主 ValueError 分支），不裸抛
    early = agent.generate_proposal()
    assert early["ok"] is False and "缺少渠道" in early["error"]

    # ③ 补齐四硬渠道（与宿主轮末落盘同形状）
    agent.repo.save_field_site(pid, {
        "name": "大理喜洲", "confirmed": True,
        "profile": {"location": "大理喜洲", "generated_at": "2026-10-14T00:00:00"}})
    agent.repo.save_informant(pid, {"code": "INF-01", "type": "村干部",
                                    "relation": "待经村干部引荐"})
    agent.repo.cache_search(pid, "itinerary::大理", "compose_itinerary", {
        "departure": "北京", "destinations": ["大理"],
        "start_date": "2026-11-01", "end_date": "2026-12-05",
        "legs": [{"from": "北京", "to": "大理", "direction": "outbound"}],
        "stays": [{"dest": "大理", "nights": 34}],
        "budget": {"total": 18000.0, "budget_total": 20000.0, "coverage": "unknown"}})

    # ④ generate：无 key 模板降级 → 八章提案 + 落盘可见
    result = agent.generate_proposal()
    assert result["ok"] is True, result.get("error")
    assert result["mode"] == "global"
    assert result["sections"] == 8, "无 key 稿件必须是八章模板"

    docs = agent.repo.list_proposals(pid)
    assert docs, "save_proposal 后 list_proposals 应可见（status/导出链可用）"
    assert result["id"] == docs[-1]["id"]
    markdown = docs[-1]["content"]
    assert [ln for ln in markdown.splitlines() if ln.startswith("## ")] == [
        f"## {heading}" for heading in PROPOSAL_SECTIONS]
    assert "模板降级" in markdown, "无 key 稿件需显式标注模板降级"
    on_disk = Path(fake_config.projects_dir) / pid / "output" / docs[-1]["filename"]
    assert on_disk.exists() and on_disk.read_text(encoding="utf-8") == markdown


# ------------------------------------------- 工具降级追问指令（防"只标注就停"）


def test_tool_content_degraded_appends_followup():
    """degraded 工具结果附追问指令：LLM 必须转为引导性提问，不停在单方陈述。

    回归：profile_field_site 返回 degraded+城市级无关 POI 后，回复停在
    “必须换路径锁定”——标注了质量缺口但没有向用户提任何问题，对话中断。
    """
    env = Envelope(ok=True, data={"items": []}, degraded="交通段失败；活动段失败")
    c = _tool_content("profile_field_site", env)
    assert "引导性提问" in c
    assert c.startswith("{"), "JSON 摘要在前，指令附后"


def test_tool_content_failed_appends_followup():
    env = Envelope(ok=False, error=ToolError(code="flyai_unavailable", message="no cli",
                                              hint="装 CLI"))
    assert "引导性提问" in _tool_content("search_hotels", env)


def test_tool_content_normal_no_followup():
    """正常结果不附指令（避免每轮注入噪声）。"""
    env = Envelope(ok=True, data={"items": [{"name": "x"}]})
    assert "引导性提问" not in _tool_content("search_hotels", env)


def test_summarize_items_keep_price_rating():
    """列表摘要保留 name+price/rating 关键标量：给 LLM 真数据可引，防幻觉补全。

    回归：search_hotels 采样经时窗摘要只透出 hotel_min=28，后续零工具轮 LLM
    把“¥28/晚（青旅级）”膨胀成三家不存在的青旅（真缓存是“尘外民宿¥28/
    子栖小筑青舍¥35”）。摘要有真细节，LLM 才不会编。
    """
    env = Envelope(ok=True, data={"items": [
        {"name": "尘外民宿(浦西万达广场店)", "price": 28.0, "rating": 4.8},
        {"name": "子栖小筑青舍", "price": 35.0},
        {"name": "无名"},
        "非dict条目",
    ]})
    s = _summarize("search_hotels", env)
    assert s["top_items"] == [
        {"name": "尘外民宿(浦西万达广场店)", "price": 28.0, "rating": 4.8},
        {"name": "子栖小筑青舍", "price": 35.0},
        {"name": "无名"},
    ]


# ------------------------------------------- 数组参数字符串化的定向救援（轮 3 实测）

def test_orchestrator_dispatch_coerces_json_string_list(fake_config, fake_flyai):
    """LLM 把 list 参数序列化成 JSON 字符串（实测 interests='["宗族祠堂", …]'
    触发 "Input should be a valid list"）→ 校验失败先定向救援：字符串能
    json.loads 出 list 则转换重试，正常进入 handler。"""
    from fieldwork_agent.core.orchestrator import FieldAgent

    agent = FieldAgent(fake_config)
    agent.flyai = fake_flyai
    env = agent.client_kernel_dispatch("profile_field_site", {
        "location": "晋江",
        "interests": '["宗族祠堂", "祭祀仪式", "侨乡"]',
    })
    assert env.ok is True
    # culture 段逐 interest 调 search_pois——字符串已被救成真 list
    pois = [kw for name, kw in fake_flyai.calls if name == "search_pois"]
    assert any(kw.get("category") == "宗族祠堂" for kw in pois)


def test_orchestrator_dispatch_coerce_unparseable_still_fails(fake_config):
    """救援不越界：非 JSON 字符串（解析不出 list）仍按 invalid_arguments 拒绝。"""
    from fieldwork_agent.core.orchestrator import FieldAgent

    agent = FieldAgent(fake_config)
    env = agent.client_kernel_dispatch("profile_field_site", {
        "location": "晋江", "interests": "宗族祠堂、祭祀仪式"})
    assert env.ok is False and env.error.code == "invalid_arguments"
