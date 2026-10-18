"""Task 2：dispatch 能力分派 + 统一包络（保留键 llm/client 按 fn 签名声明注入）。"""
import inspect
import json
import logging
import time

import pytest
from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem, ToolError
from fieldwork_agent.kernel.registry import CapabilityMeta
from fieldwork_agent.mcp_server import dispatch

# 文档组：消费注入的 llm；flyai 系（8 搜索 + 2 聚合）消费 client、
# amap 系六原语消费 amap_client（两种 client 分道，防串味）
DOCUMENT_TOOLS = {"compose_proposal", "compose_interview_guide", "suggest_informants"}
AMAP_TOOLS_PREFIX = "amap_"

# 终审③ 的替身：C 内建 ``time.time`` 无 __text_signature__，``inspect.signature`` 抛
# ValueError（CPython 3.12 实测；``print`` 有 Argument Clinic 签名，取不到签名不成立）。
UNINSPECTABLE_FN = time.time


def _meta(name: str, fn) -> CapabilityMeta:
    """构造活注册表条目（args_model 只为满足 dataclass 形状，dispatch 不消费它）。"""
    return CapabilityMeta(name=name, description="测试条目", fn=fn, args_model=ToolError)


def _patch_both_tables(monkeypatch, name: str, fn) -> None:
    """把替身塞进两张分派表。

    ``_resolve`` 先看 ``_CLIENTLESS_SINGLETONS``（server 组装时填真实 partial，模块级
    状态跨测试残留），只 patch ``_CAPABILITY_FNS`` 可能仍打到真实 handler——测试
    一律两表同换，确保离线、不触真 fn、不起 subprocess。
    """
    monkeypatch.setitem(dispatch._CAPABILITY_FNS, name, fn)
    monkeypatch.setitem(dispatch._CLIENTLESS_SINGLETONS, name, fn)


class Echo:
    """搜索原语替身：记录收到的参数并返回包络（证明分派只透传业务 kwargs）。"""

    def __init__(self):
        self.got = None

    def search_hotels(self, dest_name, **kw):
        self.got = (dest_name, kw)
        return Envelope(ok=True, data={"echo": dest_name})


def test_call_capability_validates_and_trims_kwargs(monkeypatch):
    monkeypatch.setitem(dispatch._CLIENTLESS_SINGLETONS, "search_hotels",
                        Echo().search_hotels)   # 表内存可调用 handler，不是对象
    out = dispatch.call_capability("search_hotels", {"dest_name": "大理",
                                                     "hotel_types": "民宿"}, ctx={})
    assert out["ok"] is True and out["data"]["echo"] == "大理"


def test_unknown_tool_envelope():
    out = dispatch.call_capability("nope", {}, ctx={})
    assert out["ok"] is False and out["error"]["code"] == dispatch.UNKNOWN_TOOL


def test_live_registry_fallback_resolves_late_registration(monkeypatch):
    """终审⑤/M1：两张导入期快照都未命中时回落活注册表——延迟注册的能力仍可 tools/call。

    不改这条回落，``mapping.capability_tools()``（每次活取注册表）广告出的新名字
    会被 ``call_capability`` 答 unknown_tool，tools/list 与 tools/call 不对称。
    """
    seen = {}

    def late_fn(query: str = "") -> Envelope:
        seen["query"] = query
        return Envelope(ok=True, data={"echo": query})

    monkeypatch.setattr(dispatch, "capabilities",
                        lambda: {"late_registered": _meta("late_registered", late_fn)})
    assert "late_registered" not in dispatch._CAPABILITY_FNS   # 前提：快照里确实没有

    out = dispatch.call_capability("late_registered", {"query": "田野"}, ctx={})
    assert out == {"ok": True, "data": {"echo": "田野"}}
    assert seen["query"] == "田野"
    # 回落也查不到才算未知工具（不得把回落写成"总是可调用"）
    miss = dispatch.call_capability("late_registered_typo", {}, ctx={})
    assert miss["ok"] is False and miss["error"]["code"] == dispatch.UNKNOWN_TOOL


def test_snapshot_table_wins_over_live_registry(monkeypatch):
    """表命中优先：回落只在快照未命中时发生，既有 monkeypatch 替身语义不变。"""
    # 两表同换：create_server 会往 _CLIENTLESS_SINGLETONS 填真实 partial 且模块级残留，
    # _resolve 又优先查这张表——只换 _CAPABILITY_FNS 会顺序依赖（先跑 server_contract 即红）。
    _patch_both_tables(monkeypatch, "search_keyword",
                       lambda **kw: {"from": "snapshot"})
    monkeypatch.setattr(dispatch, "capabilities", lambda: {
        "search_keyword": _meta("search_keyword", lambda **kw: {"from": "live"})})

    out = dispatch.call_capability("search_keyword", {"query": "田野"}, ctx={})
    assert out["ok"] is True and out["data"] == {"from": "snapshot"}


def test_invalid_args_envelope(monkeypatch):
    """终审②：INVALID_ARGS 的两个来源都用替身钉死——不触达真实 handler、不起 subprocess。

    原首行以 ``dest_name=1`` 调真实 ``search_hotels``：既无断言，又把"离线安全"押在
    CPython 参数转换先于 spawn 的实现细节上。替身同时覆盖两条路径：
    体内校验抛 TypeError（pydantic/类型校验的同款出口）、以及 ``fn(**arguments)`` 绑定缺参。
    """
    def fake_search(dest_name, **kw):
        raise TypeError("dest_name 须为非空字符串")
    _patch_both_tables(monkeypatch, "search_hotels", fake_search)

    out = dispatch.call_capability("search_hotels", {"dest_name": 1, "hotel_types": None},
                                   ctx={})
    assert out["ok"] is False and out["error"]["code"] == dispatch.INVALID_ARGS
    assert out["error"]["hint"]   # 客户端可自救：hint 指向 inputSchema

    out2 = dispatch.call_capability("search_hotels", {}, ctx={})   # 缺参 → 绑定失败
    assert out2["ok"] is False and out2["error"]["code"] == dispatch.INVALID_ARGS


def test_llm_injected_only_when_accepted(monkeypatch):
    calls = {}

    def fake_proposal(*, llm=None, **kw):        # 与真实 handler 同款：显式声明 keyword-only llm
        calls["llm"] = llm
        calls.update(kw)
        return Envelope(ok=True, data={"markdown": "x"})
    monkeypatch.setitem(dispatch._CAPABILITY_FNS, "compose_proposal", fake_proposal)
    sentinel = object()
    dispatch.call_capability("compose_proposal", {"ctx": {}, "sites": [],
                                                  "informants": [{"code": "P1"}],
                                                  "itinerary": {"legs": []},
                                                  "knowledge": []}, ctx={"llm": sentinel})
    assert calls["llm"] is sentinel  # 签名含 llm 才注入


def test_reserved_keys_never_leak_into_var_keyword_fn(monkeypatch):
    """未声明保留键的 ``**kwargs`` 型 fn 不得收到 llm/client——脏参透传到下游严格签名会变成假 invalid_args。"""
    calls = {}

    def fake_search(**kw):
        calls.update(kw)
        return Envelope(ok=True, data={"count": len(kw)})
    _patch_both_tables(monkeypatch, "search_hotels", fake_search)
    out = dispatch.call_capability("search_hotels", {"dest_name": "大理"},
                                   ctx={"llm": object(), "client": object()})
    assert out["ok"] is True
    assert set(calls) == {"dest_name"}


def test_client_supplied_reserved_keys_are_stripped(monkeypatch):
    """信任边界：客户端 ``arguments`` 里塞入的保留键必须被剥离，宿主 ``ctx`` 是唯一来源。

    评审 Important#1：Task 5 会把 MCP 原始 arguments 直接透传，若不剥离，
    ``{"client":"x"}`` 会让 flyai.py 把字符串当 FlyAIClient 用（AttributeError→
    internal_error），与 ``RESERVED_KEYS`` 注释承诺相反。ctx 提供时应以 ctx 为准；
    ctx 未提供时也不得让客户端值蒙混注入 fn 形参默认。
    """
    calls = {}

    def fake_search(dest_name, *, llm=None, client=None, **kw):
        # 即便 fn 显式声明了这两个形参，客户端塞的值也不能穿透；
        # dispatch 未从 ctx 回填时，fn 只能看到自己的默认 None（走自建降级）。
        calls["dest_name"] = dest_name
        calls["llm"] = llm
        calls["client"] = client
        calls["extra"] = kw
        return Envelope(ok=True, data={"ok": 1})
    _patch_both_tables(monkeypatch, "search_hotels", fake_search)

    arguments = {"dest_name": "大理", "llm": "客户端伪装", "client": "客户端伪装"}
    snapshot = dict(arguments)   # 断言不污染调用方 dict：dispatch 必须工作在副本上

    out = dispatch.call_capability("search_hotels", arguments, ctx={})

    assert out["ok"] is True
    # 客户端塞入值被剥离后，fn 看到的是自己声明的默认 None，而不是客户端字符串
    assert calls["llm"] is None
    assert calls["client"] is None
    assert calls["extra"] == {}
    # 顺带钉住：dispatch 通过 ``dict(arguments)`` 复制后才动手，原参数不受影响
    assert arguments == snapshot


def test_client_injected_only_when_ctx_provides_it(monkeypatch):
    """ctx 提供 client 才注入；ctx 没有就不注入，让 fn 走自建 FlyAIClient() 默认。"""
    seen = {}

    def fake_profile(*, client=None, **kw):
        seen["client"] = client
        seen.update(kw)
        return Envelope(ok=True, data={"profile": 1})
    _patch_both_tables(monkeypatch, "profile_field_site", fake_profile)
    sentinel = object()
    dispatch.call_capability("profile_field_site", {"location": "大理"},
                             ctx={"client": sentinel})
    assert seen["client"] is sentinel
    dispatch.call_capability("profile_field_site", {"location": "丽江"}, ctx={})
    assert seen["client"] is None


def test_amap_client_reserved_key_stripped_and_injected(monkeypatch):
    """amap_client 与 client 分道：客户端塞值剥离，ctx 提供时只注入 fn 声明的键。

    防两类串味：amap 工具收到 FlyAIClient（或反之）——server 组装会把两种
    client 都放进 ctx，若 dispatch 无差别注入，下游 AttributeError 会被折成
    假 internal_error，掩盖接线缺陷。
    """
    seen = {}

    def fake_amap(*, amap_client=None, **kw):
        seen["amap_client"] = amap_client
        seen.update(kw)
        return Envelope(ok=True, data={"echo": 1})

    _patch_both_tables(monkeypatch, "amap_geocode", fake_amap)
    flyai_sentinel, amap_sentinel = object(), object()

    out = dispatch.call_capability(
        "amap_geocode", {"address": "大理", "amap_client": "客户端伪装"},
        ctx={"client": flyai_sentinel, "amap_client": amap_sentinel})

    assert out["ok"] is True
    # ctx 提供 → 注入 sentinel；客户端塞的字符串被剥离（不会出现在任何形参/kwargs）
    assert seen["amap_client"] is amap_sentinel
    # flyai 的 client 键不穿入 amap 工具：未被声明，不入参也不入 **kwargs
    assert "client" not in seen


def test_internal_error_keeps_exception_message(monkeypatch):
    """业务异常折进包络，禁止吞错：INTERNAL 必须保留异常消息。"""

    def boom(**kw):
        raise RuntimeError("flyai CLI 崩了")
    _patch_both_tables(monkeypatch, "search_ai", boom)
    out = dispatch.call_capability("search_ai", {"query": "大理 民宿"}, ctx={})
    assert out["ok"] is False and out["error"]["code"] == dispatch.INTERNAL
    assert "flyai CLI 崩了" in out["error"]["message"]


def test_uninspectable_signature_is_internal_not_invalid_args(monkeypatch):
    """终审③：签名探测必须与参数绑定分段——取不到签名是服务端缺陷，不是客户端传错参数。

    若探测仍留在 INVALID_ARGS 的 try 内，签名不可得的 callable 会被误报成
    "参数校验失败"，把宿主接线缺陷甩锅给客户端。
    """
    with pytest.raises(ValueError):
        inspect.signature(UNINSPECTABLE_FN)   # 前提自证：替身确实取不到签名
    monkeypatch.setitem(dispatch._CAPABILITY_FNS, "opaque_builtin", UNINSPECTABLE_FN)

    out = dispatch.call_capability("opaque_builtin", {"dest_name": "大理"}, ctx={})

    assert out["ok"] is False
    assert out["error"]["code"] == dispatch.INTERNAL
    assert out["error"]["code"] != dispatch.INVALID_ARGS


def test_internal_error_is_logged_with_traceback(monkeypatch, caplog):
    """终审⑥：INTERNAL 分支必须留痕（带 traceback），且库代码不自配 handler。"""

    def boom(**kw):
        raise RuntimeError("内核炸了")
    monkeypatch.setitem(dispatch._CAPABILITY_FNS, "log_probe", boom)

    with caplog.at_level(logging.ERROR, logger=dispatch.__name__):
        out = dispatch.call_capability("log_probe", {}, ctx={})

    assert out["ok"] is False and out["error"]["code"] == dispatch.INTERNAL
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors and all(r.exc_info for r in errors)   # exc_info=True：留 traceback
    assert any("log_probe" in r.getMessage() for r in errors)   # 带上工具名才可定位
    # 库代码不得配 handler/basicConfig（落地方式由宿主决定），否则重复输出/污染 stdio
    assert dispatch.logger.handlers == []


def test_envelope_dict_drops_none_fields():
    env = Envelope(ok=False, error=ToolError(code="missing_channel", message="缺少渠道: 行程"))
    assert dispatch.envelope_dict(env) == {
        "ok": False, "error": {"code": "missing_channel", "message": "缺少渠道: 行程", "hint": ""}}


def test_envelope_dict_json_serializable_and_keeps_degraded():
    env = Envelope(ok=True, degraded="单段失败已隔离",
                   data=ItemPage(items=[SearchItem(name="古城客栈", price=120.0)], total=1))
    dumped = dispatch.envelope_dict(env)
    assert json.dumps(dumped, ensure_ascii=False)
    assert dumped["degraded"] == "单段失败已隔离"
    assert dumped["data"]["items"][0]["name"] == "古城客栈"


def test_business_failure_envelope_passes_through_untouched(monkeypatch):
    """终审④：``ok=False`` 的业务包络必须原样透传，不得被改写成协议级错误码。

    业务失败（flyai 无 key 的体验降级）走包络不走协议异常；若 dispatch 把它折成
    invalid_args/internal_error 或丢掉 ``degraded``，客户端就无法区分"我传错了"
    与"服务端降级了"。整包等值断言：任何字段改写/丢失都当场暴露。
    """
    def degraded(**kw) -> Envelope:
        return Envelope(
            ok=False,
            error=ToolError(code="flyai_unavailable", message="flyai CLI 不可用",
                            hint="配置 FLYAI_API_KEY 后重试"),
            degraded="体验模式")
    _patch_both_tables(monkeypatch, "search_hotels", degraded)

    out = dispatch.call_capability("search_hotels", {"dest_name": "大理"}, ctx={})

    assert out == {"ok": False,
                   "error": {"code": "flyai_unavailable", "message": "flyai CLI 不可用",
                             "hint": "配置 FLYAI_API_KEY 后重试"},
                   "degraded": "体验模式"}


def test_non_envelope_result_is_wrapped(monkeypatch):
    _patch_both_tables(monkeypatch, "search_keyword", lambda **kw: {"raw": 1})
    out = dispatch.call_capability("search_keyword", {"query": "田野"}, ctx={})
    assert out == {"ok": True, "data": {"raw": 1}}


def test_registry_fns_expose_injection_seams():
    """裸导入即注册：21 能力全进分派表；文档能力声明 llm、flyai 系声明 client、
    amap 系六原语与聚合能力 plan_last_mile 声明 amap_client（注入接缝分道，
    0c5b7bb 已落地）。"""
    fns = dispatch._CAPABILITY_FNS
    assert len(fns) == 21 and DOCUMENT_TOOLS <= set(fns)
    amap_tools = {n for n in fns if n.startswith(AMAP_TOOLS_PREFIX)} | {"plan_last_mile"}
    assert len(amap_tools) == 7
    for name in DOCUMENT_TOOLS:
        assert "llm" in inspect.signature(fns[name]).parameters
    for name in amap_tools:
        assert "amap_client" in inspect.signature(fns[name]).parameters
    for name in set(fns) - DOCUMENT_TOOLS - amap_tools:
        assert "client" in inspect.signature(fns[name]).parameters
