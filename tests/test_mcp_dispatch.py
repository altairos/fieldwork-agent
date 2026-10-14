"""Task 2：dispatch 能力分派 + 统一包络（保留键 llm/client 按 fn 签名声明注入）。"""
import inspect
import json

from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem, ToolError
from fieldwork_agent.mcp_server import dispatch

# 文档组：消费注入的 llm；其余（8 搜索 + 2 聚合）消费注入的 client
DOCUMENT_TOOLS = {"compose_proposal", "compose_interview_guide", "suggest_informants"}


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


def test_invalid_args_envelope():
    # 多余/强转参数不报错（真实 fn 只会走 CLI 降级），只有缺参才算 invalid：
    dispatch.call_capability("search_hotels", {"dest_name": 1, "hotel_types": None}, ctx={})
    out2 = dispatch.call_capability("search_hotels", {}, ctx={})
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
    monkeypatch.setitem(dispatch._CAPABILITY_FNS, "search_hotels", fake_search)
    out = dispatch.call_capability("search_hotels", {"dest_name": "大理"},
                                   ctx={"llm": object(), "client": object()})
    assert out["ok"] is True
    assert set(calls) == {"dest_name"}


def test_client_injected_only_when_ctx_provides_it(monkeypatch):
    """ctx 提供 client 才注入；ctx 没有就不注入，让 fn 走自建 FlyAIClient() 默认。"""
    seen = {}

    def fake_profile(*, client=None, **kw):
        seen["client"] = client
        seen.update(kw)
        return Envelope(ok=True, data={"profile": 1})
    monkeypatch.setitem(dispatch._CAPABILITY_FNS, "profile_field_site", fake_profile)
    sentinel = object()
    dispatch.call_capability("profile_field_site", {"location": "大理"},
                             ctx={"client": sentinel})
    assert seen["client"] is sentinel
    dispatch.call_capability("profile_field_site", {"location": "丽江"}, ctx={})
    assert seen["client"] is None


def test_internal_error_keeps_exception_message(monkeypatch):
    """业务异常折进包络，禁止吞错：INTERNAL 必须保留异常消息。"""

    def boom(**kw):
        raise RuntimeError("flyai CLI 崩了")
    monkeypatch.setitem(dispatch._CAPABILITY_FNS, "search_ai", boom)
    out = dispatch.call_capability("search_ai", {"query": "大理 民宿"}, ctx={})
    assert out["ok"] is False and out["error"]["code"] == dispatch.INTERNAL
    assert "flyai CLI 崩了" in out["error"]["message"]


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


def test_non_envelope_result_is_wrapped(monkeypatch):
    monkeypatch.setitem(dispatch._CAPABILITY_FNS, "search_keyword", lambda **kw: {"raw": 1})
    out = dispatch.call_capability("search_keyword", {"query": "田野"}, ctx={})
    assert out == {"ok": True, "data": {"raw": 1}}


def test_registry_fns_expose_injection_seams():
    """裸导入即注册：13 能力全进分派表；文档能力声明 llm、搜索/聚合声明 client（注入接缝，0c5b7bb 已落地）。"""
    fns = dispatch._CAPABILITY_FNS
    assert len(fns) == 13 and DOCUMENT_TOOLS <= set(fns)
    for name in DOCUMENT_TOOLS:
        assert "llm" in inspect.signature(fns[name]).parameters
    for name in set(fns) - DOCUMENT_TOOLS:
        assert "client" in inspect.signature(fns[name]).parameters
