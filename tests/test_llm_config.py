"""kernel.llm + Config 模型三槽 — 全 mock，不联网、不需要真实 API Key。"""
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fieldwork_agent.kernel import config as config_mod
from fieldwork_agent.kernel import llm as llm_mod
from fieldwork_agent.kernel.config import Config
from fieldwork_agent.kernel.llm import LLMClient, classify_llm_error

# ---------------------------------------------------------------- Config 三槽


def _patch_dirs(monkeypatch, tmp_path):
    """R6：CONFIG_DIR / PROJECTS_DIR 同时指向 tmp_path 下不存在的路径，
    避免 save()/ensure_dirs() 在盘根 mkdir，Windows/CI 双平台安全。"""
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / "cfg_dir")
    monkeypatch.setattr(config_mod, "PROJECTS_DIR", tmp_path / "cfg_dir" / "projects")


def test_config_model_slots(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setenv("FA_INTERVIEW_MODEL", "qwen3.8-flash")
    monkeypatch.setenv("FA_PROPOSAL_MODEL", "qwen-max")
    monkeypatch.delenv("FA_EXTRACT_MODEL", raising=False)
    cfg = Config()
    assert cfg.llm_models == {"interview": "qwen3.8-flash", "proposal": "qwen-max",
                              "extract": "qwen3.8-flash"}


def test_config_slot_defaults(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    for var in ("FA_INTERVIEW_MODEL", "FA_PROPOSAL_MODEL", "FA_EXTRACT_MODEL"):
        monkeypatch.delenv(var, raising=False)
    cfg = Config()
    assert cfg.interview_model == "qwen3.8-flash"
    assert cfg.proposal_model == "qwen-max"
    assert cfg.extract_model == "qwen3.8-flash"


def test_config_json_overrides_env(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setenv("FA_EXTRACT_MODEL", "env-model")
    (tmp_path / "cfg_dir").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cfg_dir" / "config.json").write_text(
        json.dumps({"extract_model": "file-model"}), encoding="utf-8",
    )
    cfg = Config()
    assert cfg.extract_model == "file-model"  # config.json 键同名覆盖 env


# ---------------------------------------------------------------- LLMClient 构造契约


def test_llm_client_requires_api_key():
    with pytest.raises(RuntimeError, match="api_key"):
        LLMClient(api_key="", models={"proposal": "m"})


def test_llm_client_sdk_missing(monkeypatch):
    monkeypatch.setattr(llm_mod, "_DASHSCOPE_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="SDK"):
        LLMClient(api_key="k", models={"proposal": "m"})


# ---------------------------------------------------------------- chat 解析


def _fake_response(status_code=200, *, content="", tool_calls=None,
                   finish_reason="stop", usage=None, message=""):
    """构造与 dashscope Generation 返回同形的假响应。"""
    msg_attrs = {"content": content}
    if tool_calls is not None:
        msg_attrs["tool_calls"] = tool_calls
    choice = type("C", (), {
        "finish_reason": finish_reason,
        "message": type("M", (), msg_attrs)(),
    })()
    output = type("O", (), {"choices": [choice]})()
    return type("R", (), {
        "status_code": status_code,
        "output": output,
        "usage": usage if usage is not None else {},
        "message": message,
    })()


def test_llm_chat_parses_tool_calls(monkeypatch):
    resp = _fake_response(
        content="",
        tool_calls=[{"id": "c1", "function": {
            "name": "search_hotels", "arguments": '{"dest_name":"大理"}'}}],
        finish_reason="tool_calls",
    )
    monkeypatch.setattr(llm_mod.Generation, "call", lambda **kw: resp)
    client = LLMClient(api_key="k", models={"proposal": "m"})
    out = client.chat([{"role": "user", "content": "hi"}], slot="proposal", tools=[{}])
    assert out["tool_calls"][0]["name"] == "search_hotels"
    assert out["tool_calls"][0]["arguments"] == {"dest_name": "大理"}
    assert out["tool_calls"][0]["id"] == "c1"
    assert out["finish_reason"] == "tool_calls"


def test_llm_chat_plain_content(monkeypatch):
    resp = _fake_response(content="你好", usage={"total_tokens": 7})
    seen = {}

    def fake_call(**kw):
        seen.update(kw)
        return resp

    monkeypatch.setattr(llm_mod.Generation, "call", fake_call)
    client = LLMClient(api_key="k", models={"extract": "m-ex"})
    out = client.chat([{"role": "user", "content": "hi"}], slot="extract")
    assert out["content"] == "你好"
    assert out["tool_calls"] is None
    assert out["finish_reason"] == "stop"
    assert out["usage"] == {"total_tokens": 7}
    assert seen["model"] == "m-ex"      # 按槽位选模型
    assert seen["api_key"] == "k"
    assert "tools" not in seen          # 无 tools 时不传该参数


def test_llm_chat_error_status(monkeypatch):
    """非 200 不抛异常，但契约四键恒在（宿主直读 out["usage"] 不得 KeyError）。"""
    resp = _fake_response(status_code=400, message="bad request")
    monkeypatch.setattr(llm_mod.Generation, "call", lambda **kw: resp)
    client = LLMClient(api_key="k", models={"proposal": "m"})
    out = client.chat([{"role": "user", "content": "hi"}], slot="proposal")
    assert out == {"content": "", "tool_calls": None, "finish_reason": "",
                   "usage": {}, "error": "bad request"}


def test_llm_chat_bad_arguments_json_tolerated(monkeypatch):
    """arguments 非法 JSON → 丢弃该条；全部非法则 tool_calls 为 None，不抛异常。"""
    resp = _fake_response(
        tool_calls=[{"id": "c1", "function": {"name": "x", "arguments": "{not json"}}],
        finish_reason="tool_calls",
    )
    monkeypatch.setattr(llm_mod.Generation, "call", lambda **kw: resp)
    client = LLMClient(api_key="k", models={"proposal": "m"})
    out = client.chat([{"role": "user", "content": "hi"}], slot="proposal", tools=[{}])
    assert out["tool_calls"] is None


# ---------------------------------------------------------------- chat_stream 流式


def test_chat_stream_yields_deltas_then_final():
    """mock DashScope stream 响应：逐 chunk yield delta，最终 yield 四键。"""
    client = LLMClient.__new__(LLMClient)
    client.api_key = "fake"
    client.models = {"interview": "q", "proposal": "q", "extract": "q"}

    chunks = [
        MagicMock(status_code=200, output=MagicMock(choices=[
            MagicMock(message=MagicMock(content="你", tool_calls=None),
                      finish_reason="null")]), usage=None),
        MagicMock(status_code=200, output=MagicMock(choices=[
            MagicMock(message=MagicMock(content="好", tool_calls=None),
                      finish_reason="null")]), usage=None),
        MagicMock(status_code=200, output=MagicMock(choices=[
            MagicMock(message=MagicMock(content="", tool_calls=None),
                      finish_reason="stop")]), usage={"total_tokens": 10}),
    ]
    with patch("fieldwork_agent.kernel.llm.Generation") as MockGen:
        MockGen.call.return_value = iter(chunks)
        results = list(client.chat_stream([{"role": "user", "content": "hi"}],
                                          slot="interview"))
    deltas = [r for r in results if "delta" in r]
    assert "".join(d["delta"] for d in deltas) == "你好"
    final = results[-1]
    assert final["content"] == "你好" and final["finish_reason"] == "stop"
    assert final["usage"] == {"total_tokens": 10}
    assert final["tool_calls"] is None


# ------------------------------------------------ 关闭思考模式（qwen3 混合思考模型）
# qwen3.8/3.7/3.6/3.5 系混合思考模型默认开思考：思考流走 reasoning_content、
# 正文走 content，本层只解析 content → 工具结果回填后的最终轮「只想不写」时
# 正文为空（用户侧表现为无回复即结束）。修复：对支持该参数的模型在双路径
# （SDK 原生 + OpenAI 兼容）显式传 enable_thinking=False；非思考模型
# （qwen-max 等）与仅思考模型（-thinking 后缀，只接受 true）传了会被网关
# 400 NotSupportEnableThinking 拒绝，须按模型名门控。


def test_sdk_chat_disables_thinking_for_qwen3(monkeypatch):
    resp = _fake_response(content="ok")
    seen = {}

    def fake_call(**kw):
        seen.update(kw)
        return resp

    monkeypatch.setattr(llm_mod.Generation, "call", fake_call)
    client = LLMClient(api_key="k", models={"interview": "qwen3.8-flash"})
    client.chat([{"role": "user", "content": "hi"}], slot="interview")
    assert seen["enable_thinking"] is False


def test_sdk_chat_stream_disables_thinking_for_qwen3():
    client = LLMClient.__new__(LLMClient)
    client.api_key = "fake"
    client.models = {"interview": "qwen3.8-flash"}
    chunks = [MagicMock(status_code=200, output=MagicMock(choices=[
        MagicMock(message=MagicMock(content="", tool_calls=None),
                  finish_reason="stop")]), usage={})]
    with patch("fieldwork_agent.kernel.llm.Generation") as MockGen:
        MockGen.call.return_value = iter(chunks)
        list(client.chat_stream([{"role": "user", "content": "hi"}],
                                slot="interview"))
        assert MockGen.call.call_args.kwargs["enable_thinking"] is False


def test_compat_payload_disables_thinking_for_qwen3():
    client = LLMClient(api_key="k", models={"interview": "qwen3.8-flash"},
                       base_url=_COMPAT_BASE)
    for stream in (False, True):
        payload = client._compat_request(
            [{"role": "user", "content": "hi"}], slot="interview",
            tools=None, temperature=0.7, max_tokens=64, stream=stream)
        assert payload["enable_thinking"] is False


@pytest.mark.parametrize("model", ["qwen-max", "qwen-plus",
                                   "qwen3-vl-235b-a22b-thinking"])
def test_thinking_toggle_omitted_for_unsupported_models(monkeypatch, model):
    """非思考模型与仅思考模型：双路径均不传 enable_thinking（传了网关 400）。"""
    resp = _fake_response(content="ok")
    seen = {}

    def fake_call(**kw):
        seen.update(kw)
        return resp

    monkeypatch.setattr(llm_mod.Generation, "call", fake_call)
    client = LLMClient(api_key="k", models={"proposal": model})
    client.chat([{"role": "user", "content": "hi"}], slot="proposal")
    assert "enable_thinking" not in seen
    payload = client._compat_request(
        [{"role": "user", "content": "hi"}], slot="proposal",
        tools=None, temperature=0.7, max_tokens=64, stream=True)
    assert "enable_thinking" not in payload


# ---------------------------------------------------------------- 套餐端点（plan 映射）
# 百炼三套凭证体系：按量付费 sk-（SDK 原生默认）与套餐 sk-sp-（Coding/Token，
# 各配专属 OpenAI 兼容端点）不互通，混用 401「Invalid API-key provided.」。


def test_plan_base_url_whitelist():
    """套餐类型 → 白名单端点；空/default/未知（含任意 URL）→ None 走 SDK 原生。"""
    from fieldwork_agent.kernel.llm import plan_base_url

    assert plan_base_url("coding") == "https://coding.dashscope.aliyuncs.com/v1"
    assert plan_base_url("Token") == (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
    assert plan_base_url("") is None
    assert plan_base_url("default") is None
    assert plan_base_url("https://evil.example.com/v1") is None


def test_config_dashscope_plan_env(tmp_path, monkeypatch):
    """FA_DASHSCOPE_PLAN：服务端全局套餐类型，默认空（按量付费走 SDK 原生）。"""
    _patch_dirs(monkeypatch, tmp_path)
    monkeypatch.setenv("FA_DASHSCOPE_PLAN", "coding")
    assert Config().dashscope_plan == "coding"
    monkeypatch.delenv("FA_DASHSCOPE_PLAN", raising=False)
    assert Config().dashscope_plan == ""


# ---------------------------------------------------------------- OpenAI 兼容协议（套餐端点）
# base_url 非空时 httpx 直连 {base_url}/chat/completions，契约与 SDK 原生路径
# 完全一致（四键恒在 + error 通道），消费方（loop）无感切换。

_COMPAT_BASE = "https://coding.dashscope.aliyuncs.com/v1"


def _compat_response(status_code=200, json_body=None, text=""):
    """构造与 httpx.Response 同形的假响应。"""
    return SimpleNamespace(status_code=status_code, json=lambda: json_body, text=text)


class _FakeStreamCM:
    """httpx.stream(...) 返回的上下文管理器假体。"""

    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(self, *exc):
        return False


def _sse_line(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False)


def test_compat_chat_request_shape_and_parse(monkeypatch):
    """base_url 非空走 OpenAI 兼容：URL/鉴权/请求体形状 + 响应解析同契约。"""
    seen = {}

    def fake_post(url, *, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, json=json)
        return _compat_response(200, {
            "choices": [{"message": {
                "role": "assistant", "content": "你好",
                "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "search_hotels", "arguments": '{"dest_name":"大理"}'}}],
            }, "finish_reason": "tool_calls"}],
            "usage": {"total_tokens": 9},
        })

    monkeypatch.setattr(llm_mod.httpx, "post", fake_post)
    client = LLMClient(api_key="sk-sp-x", models={"proposal": "qwen-max"},
                       base_url=_COMPAT_BASE)
    out = client.chat([{"role": "user", "content": "hi"}], slot="proposal",
                      tools=[{"type": "function", "function": {"name": "f"}}])
    assert seen["url"] == f"{_COMPAT_BASE}/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-sp-x"
    assert seen["json"]["model"] == "qwen-max"
    assert "tools" in seen["json"]
    assert out["content"] == "你好"
    assert out["tool_calls"][0]["arguments"] == {"dest_name": "大理"}
    assert out["finish_reason"] == "tool_calls"
    assert out["usage"] == {"total_tokens": 9}
    assert "error" not in out


def test_compat_chat_no_tools_omits_key(monkeypatch):
    seen = {}

    def fake_post(url, *, headers=None, json=None, timeout=None):
        seen.update(json=json)
        return _compat_response(200, {"choices": [{"message": {"content": "好"},
                                                   "finish_reason": "stop"}]})

    monkeypatch.setattr(llm_mod.httpx, "post", fake_post)
    client = LLMClient(api_key="k", models={"extract": "m"}, base_url=_COMPAT_BASE)
    out = client.chat([{"role": "user", "content": "hi"}], slot="extract")
    assert "tools" not in seen["json"]
    assert out["tool_calls"] is None


def test_compat_chat_error_status(monkeypatch):
    """401（套餐 Key 配错端点的实际报文）→ 四键零值 + error 逐字透传。"""
    monkeypatch.setattr(llm_mod.httpx, "post", lambda url, **kw: _compat_response(
        401, {"error": {"code": "InvalidApiKey",
                        "message": "Invalid API-key provided."}}))
    client = LLMClient(api_key="k", models={"proposal": "m"}, base_url=_COMPAT_BASE)
    out = client.chat([{"role": "user", "content": "hi"}], slot="proposal")
    assert out == {"content": "", "tool_calls": None, "finish_reason": "",
                   "usage": {}, "error": "Invalid API-key provided."}


def test_compat_chat_error_nonjson_body(monkeypatch):
    """错误 body 非法 JSON（网关 5xx 页面等）→ 回退 text 片段，不抛异常。"""

    def _raise():
        raise ValueError("no json")

    resp = SimpleNamespace(status_code=502, json=_raise, text="<html>Bad Gateway</html>")
    monkeypatch.setattr(llm_mod.httpx, "post", lambda url, **kw: resp)
    client = LLMClient(api_key="k", models={"proposal": "m"}, base_url=_COMPAT_BASE)
    out = client.chat([{"role": "user", "content": "hi"}], slot="proposal")
    assert "Bad Gateway" in out["error"]


def test_compat_chat_stream_deltas_toolcalls_usage(monkeypatch):
    """SSE 流：content 增量→delta 帧；tool_calls 跨 chunk 按 index 合并；usage/终止帧。"""
    chunks = [{'choices': [{'delta': {'content': '你'}, 'finish_reason': None}]},
     {'choices': [{'delta': {'content': '好'}, 'finish_reason': None}]},
     {'choices': [{'delta': {'tool_calls': [{'index': 0,
                                             'id': 'c1',
                                             'type': 'function',
                                             'function': {'name': 'search_hotels',
                                                          'arguments': '{"dest'}}]}}]},
     {'choices': [{'delta': {'tool_calls': [{'index': 0,
                                             'function': {'arguments': '_name":"大理"}'}}]}}]},
     {'choices': [{'delta': {}, 'finish_reason': 'tool_calls'}],
      'usage': {'total_tokens': 12}}]
    lines = [_sse_line(c) for c in chunks] + ["data: [DONE]"]
    resp = SimpleNamespace(status_code=200, iter_lines=lambda: iter(lines))
    monkeypatch.setattr(llm_mod.httpx, "stream", lambda *a, **kw: _FakeStreamCM(resp))
    client = LLMClient(api_key="k", models={"interview": "q"}, base_url=_COMPAT_BASE)
    results = list(client.chat_stream([{"role": "user", "content": "hi"}],
                                      slot="interview"))
    deltas = [r for r in results if "delta" in r]
    assert [d["delta"] for d in deltas] == ["你", "好"]
    final = results[-1]
    assert final["content"] == "你好"
    assert final["tool_calls"] == [{
        "id": "c1", "name": "search_hotels", "arguments": {"dest_name": "大理"}}]
    assert final["finish_reason"] == "tool_calls"
    assert final["usage"] == {"total_tokens": 12}


def test_compat_chat_stream_error_status(monkeypatch):
    """流式路径 HTTP 非 200：同样 yield 四键零值 + error，不抛异常。"""
    resp = SimpleNamespace(status_code=401, iter_lines=lambda: iter([]),
                           read=lambda: None,
                           json=lambda: {"error": {"message": "Invalid API-key provided."}})
    monkeypatch.setattr(llm_mod.httpx, "stream", lambda *a, **kw: _FakeStreamCM(resp))
    client = LLMClient(api_key="k", models={"interview": "q"}, base_url=_COMPAT_BASE)
    results = list(client.chat_stream([{"role": "user", "content": "hi"}],
                                      slot="interview"))
    assert results == [{"content": "", "tool_calls": None, "finish_reason": "",
                        "usage": {}, "error": "Invalid API-key provided."}]


def test_compat_chat_stream_error_real_httpx_stream(monkeypatch):
    """真实 httpx 流式响应（未 read）非 200：错误帧须产出，不抛 RuntimeError。

    回归守护：SimpleNamespace mock 的 json() 恒成功，掩盖了真实流式响应行为
    （未 read() 时 .json()/.text 均抛 "Attempted to access streaming response
    content"），曾导致用户看到 Python 异常而非真实 API 错误消息。
    """
    body = json.dumps({"error": {"code": "InvalidApiKey",
                                 "message": "Invalid API-key provided."}}).encode()
    resp = httpx.Response(
        401, stream=httpx.ByteStream(body),
        request=httpx.Request("POST", f"{_COMPAT_BASE}/chat/completions"))
    assert not resp.is_stream_consumed  # 确为未读的流式形态
    monkeypatch.setattr(llm_mod.httpx, "stream", lambda *a, **kw: _FakeStreamCM(resp))
    client = LLMClient(api_key="k", models={"interview": "q"}, base_url=_COMPAT_BASE)
    results = list(client.chat_stream([{"role": "user", "content": "hi"}],
                                      slot="interview"))
    assert results == [{"content": "", "tool_calls": None, "finish_reason": "",
                        "usage": {}, "error": "Invalid API-key provided."}]


def test_compat_base_url_requires_httpx(monkeypatch):
    monkeypatch.setattr(llm_mod, "_HTTPX_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="httpx"):
        LLMClient(api_key="k", models={"proposal": "m"}, base_url=_COMPAT_BASE)


# ------------------------------------------- 瞬时故障重试 + 错误分类


def test_classify_llm_error_kinds():
    """错误分类：额度/端点/凭证/网络/未知各归其位（顺序即优先级）。"""
    assert classify_llm_error(
        "Free quota exhausted. To continue accessing the model on a paid basis...") == "quota"
    assert classify_llm_error("url error, please check url！") == "base_url"
    assert classify_llm_error("Invalid API-key provided.") == "auth"
    assert classify_llm_error("invalid access token or token expired") == "auth"
    assert classify_llm_error(
        "网络错误：peer closed connection without sending complete message body "
        "(incomplete chunked read)") == "network"
    assert classify_llm_error("something odd") == "unknown"
    assert classify_llm_error(None) == "unknown"


def test_compat_chat_retries_transport_error(monkeypatch):
    """连接级抖动：首次抛 httpx.TransportError，第二次成功 → 重试救回。"""
    monkeypatch.setattr(llm_mod, "_LLM_RETRY_DELAY_S", 0)
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        if len(calls) == 1:
            raise httpx.ConnectError("peer closed connection")
        return _compat_response(200, {"choices": [{"message": {"content": "好"},
                                                   "finish_reason": "stop"}]})

    monkeypatch.setattr(llm_mod.httpx, "post", fake_post)
    client = LLMClient(api_key="k", models={"proposal": "m"}, base_url=_COMPAT_BASE)
    out = client.chat([{"role": "user", "content": "hi"}], slot="proposal")
    assert out["content"] == "好" and "error" not in out
    assert len(calls) == 2                      # 恰好一次重试


def test_compat_chat_retries_gateway_5xx(monkeypatch):
    """网关 502 属瞬时故障：一次退避后重试成功。"""
    monkeypatch.setattr(llm_mod, "_LLM_RETRY_DELAY_S", 0)
    responses = [_compat_response(502, None, "<html>Bad Gateway</html>"),
                 _compat_response(200, {"choices": [{"message": {"content": "好"},
                                                     "finish_reason": "stop"}]})]
    monkeypatch.setattr(llm_mod.httpx, "post", lambda url, **kw: responses.pop(0))
    client = LLMClient(api_key="k", models={"proposal": "m"}, base_url=_COMPAT_BASE)
    out = client.chat([{"role": "user", "content": "hi"}], slot="proposal")
    assert out["content"] == "好" and "error" not in out


def test_compat_chat_stream_retries_before_first_delta(monkeypatch):
    """尚未吐出正文时断流：安全重试一次（不向用户暴露半截错误）。"""
    monkeypatch.setattr(llm_mod, "_LLM_RETRY_DELAY_S", 0)

    def boom_lines():
        raise httpx.ReadError("peer closed")
        yield  # pragma: no cover

    bad = SimpleNamespace(status_code=200, iter_lines=boom_lines)
    good_lines = [_sse_line({"choices": [{"delta": {"content": "好"},
                                         "finish_reason": "stop"}]}), "data: [DONE]"]
    good = SimpleNamespace(status_code=200, iter_lines=lambda: iter(good_lines))
    cms = [_FakeStreamCM(bad), _FakeStreamCM(good)]
    monkeypatch.setattr(llm_mod.httpx, "stream", lambda *a, **kw: cms.pop(0))
    client = LLMClient(api_key="k", models={"interview": "q"}, base_url=_COMPAT_BASE)
    results = list(client.chat_stream([{"role": "user", "content": "hi"}],
                                      slot="interview"))
    assert [r["delta"] for r in results if "delta" in r] == ["好"]
    final = results[-1]
    assert final["content"] == "好" and final["finish_reason"] == "stop"
    assert "error" not in final


def test_compat_chat_stream_no_retry_after_delta(monkeypatch):
    """已吐字后断流：绝不重试（会重复正文），如实发网络错误帧。"""
    monkeypatch.setattr(llm_mod, "_LLM_RETRY_DELAY_S", 0)
    calls = []

    def lines():
        yield _sse_line({"choices": [{"delta": {"content": "你"},
                                      "finish_reason": None}]})
        raise httpx.ReadError("peer closed")

    resp = SimpleNamespace(status_code=200, iter_lines=lines)

    def fake_stream(*a, **kw):
        calls.append(1)
        return _FakeStreamCM(resp)

    monkeypatch.setattr(llm_mod.httpx, "stream", fake_stream)
    client = LLMClient(api_key="k", models={"interview": "q"}, base_url=_COMPAT_BASE)
    results = list(client.chat_stream([{"role": "user", "content": "hi"}],
                                      slot="interview"))
    assert [r["delta"] for r in results if "delta" in r] == ["你"]
    assert "网络错误" in results[-1]["error"]
    assert len(calls) == 1                      # 不重试，避免重复吐字


def test_sdk_chat_retries_transport_error(monkeypatch):
    """SDK 原生路径（未配 base_url）的连接级异常同样重试一次。"""
    import requests

    monkeypatch.setattr(llm_mod, "_LLM_RETRY_DELAY_S", 0)
    calls = []

    def fake_call(**kw):
        calls.append(1)
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError("peer closed")
        return _fake_response(content="好")

    monkeypatch.setattr(llm_mod.Generation, "call", fake_call)
    client = LLMClient(api_key="k", models={"proposal": "m"})
    out = client.chat([{"role": "user", "content": "hi"}], slot="proposal")
    assert out["content"] == "好" and "error" not in out
    assert len(calls) == 2


def test_sdk_chat_stream_retries_before_first_delta(monkeypatch):
    """SDK 流式：首帧前断流可重试；已吐字后断流不重试。"""
    import requests

    monkeypatch.setattr(llm_mod, "_LLM_RETRY_DELAY_S", 0)
    calls = []

    def boom():
        raise requests.exceptions.ConnectionError("peer closed")
        yield  # pragma: no cover

    def fake_call(**kw):
        calls.append(1)
        if len(calls) == 1:
            return boom()
        return iter([_fake_response(content="好")])

    monkeypatch.setattr(llm_mod.Generation, "call", fake_call)
    client = LLMClient(api_key="k", models={"interview": "q"})
    results = list(client.chat_stream([{"role": "user", "content": "hi"}],
                                      slot="interview"))
    assert [r["delta"] for r in results if "delta" in r] == ["好"]
    assert results[-1]["content"] == "好"
    assert len(calls) == 2


def test_sdk_chat_stream_no_retry_after_delta(monkeypatch):
    """SDK 流式已吐字后断流：不重试，如实发错误帧。"""
    import requests

    monkeypatch.setattr(llm_mod, "_LLM_RETRY_DELAY_S", 0)
    calls = []

    def partial():
        yield _fake_response(content="你", finish_reason="null")
        raise requests.exceptions.ConnectionError("peer closed")

    def fake_call(**kw):
        calls.append(1)
        return partial()

    monkeypatch.setattr(llm_mod.Generation, "call", fake_call)
    client = LLMClient(api_key="k", models={"interview": "q"})
    results = list(client.chat_stream([{"role": "user", "content": "hi"}],
                                      slot="interview"))
    assert [r["delta"] for r in results if "delta" in r] == ["你"]
    assert "网络错误" in results[-1]["error"]
    assert len(calls) == 1


# ------------------------------------------- SDK url error → 兼容端点回退


class TestUrlErrorCompatFallback:
    """网关按模型绑定 aigc 服务路径，SDK 固定打 text-generation；模型在
    multimodal 等路径时网关报 400 "url error"（消息文本误导，实为路径
    不匹配）。回退到默认端点 OpenAI 兼容入口（统一路由）。"""

    def _url_error_resp(self):
        return SimpleNamespace(
            status_code=400, code="url error",
            message="url error, please check url！ For details, see: "
                    "https://help.aliyun.com/zh/model-studio/error-code#error-url")

    def test_chat_falls_back_to_compat(self, monkeypatch):
        monkeypatch.setattr(llm_mod.Generation, "call", lambda **kw: self._url_error_resp())
        ok = SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 5}})
        urls = []

        def fake_post(url, **kw):
            urls.append(url)
            return ok

        monkeypatch.setattr(llm_mod.httpx, "post", fake_post)
        client = LLMClient(api_key="k", models={"proposal": "qwen3.8-flash"})
        out = client.chat([{"role": "user", "content": "hi"}], slot="proposal")
        assert out["content"] == "hello"
        assert out["usage"] == {"total_tokens": 5}
        assert "error" not in out
        assert urls and "compatible-mode/v1/chat/completions" in urls[0]

    def test_chat_falls_back_regardless_of_status(self, monkeypatch):
        """SDK 包装形态未必是 400：只认 "url error" 特征串。"""
        resp = SimpleNamespace(status_code=500, code="",
                               message="url error, please check url！")
        monkeypatch.setattr(llm_mod.Generation, "call", lambda **kw: resp)
        ok = SimpleNamespace(status_code=200, json=lambda: {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {}})

        def fake_post(url, **kw):
            return ok

        monkeypatch.setattr(llm_mod.httpx, "post", fake_post)
        client = LLMClient(api_key="k", models={"proposal": "m"})
        out = client.chat([{"role": "user", "content": "hi"}], slot="proposal")
        assert out["content"] == "hi"

    def test_chat_keeps_other_sdk_errors(self, monkeypatch):
        resp = SimpleNamespace(status_code=401, code="InvalidApiKey",
                               message="Invalid API-key provided.")
        monkeypatch.setattr(llm_mod.Generation, "call", lambda **kw: resp)

        def boom(url, **kw):
            raise AssertionError("非 url error 不应回退兼容端点")

        monkeypatch.setattr(llm_mod.httpx, "post", boom)
        client = LLMClient(api_key="k", models={"proposal": "m"})
        out = client.chat([{"role": "user", "content": "hi"}], slot="proposal")
        assert out["error"] == "Invalid API-key provided."
        assert out["content"] == ""

    def test_stream_falls_back_to_compat(self, monkeypatch):
        monkeypatch.setattr(llm_mod.Generation, "call",
                            lambda **kw: iter([self._url_error_resp()]))
        body = (b'data: {"choices":[{"delta":{"content":"hi"},'
                b'"finish_reason":null}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
                b'"usage":{"total_tokens":3}}\n\n'
                b'data: [DONE]\n\n')
        stream_resp = httpx.Response(
            200, stream=httpx.ByteStream(body),
            request=httpx.Request("POST", "https://x/v1/chat/completions"))
        urls = []

        class _CM:
            def __enter__(self):
                return stream_resp

            def __exit__(self, *a):
                return False

        def fake_stream(method, url, **kw):
            urls.append(url)
            return _CM()

        monkeypatch.setattr(llm_mod.httpx, "stream", fake_stream)
        client = LLMClient(api_key="k", models={"interview": "qwen3.8-flash"})
        results = list(client.chat_stream([{"role": "user", "content": "hi"}],
                                          slot="interview"))
        assert {"delta": "hi"} in results
        assert results[-1]["content"] == "hi"
        assert results[-1]["usage"] == {"total_tokens": 3}
        assert urls and "compatible-mode/v1/chat/completions" in urls[0]

    def test_stream_keeps_other_sdk_errors(self, monkeypatch):
        resp = SimpleNamespace(status_code=401, code="InvalidApiKey",
                               message="Invalid API-key provided.")
        monkeypatch.setattr(llm_mod.Generation, "call", lambda **kw: iter([resp]))

        def boom(method, url, **kw):
            raise AssertionError("非 url error 不应回退兼容端点")

        monkeypatch.setattr(llm_mod.httpx, "stream", boom)
        client = LLMClient(api_key="k", models={"interview": "m"})
        results = list(client.chat_stream([{"role": "user", "content": "hi"}],
                                          slot="interview"))
        assert results == [{"content": "", "tool_calls": None, "finish_reason": "",
                            "usage": {}, "error": "Invalid API-key provided."}]


# ------------------------------------------------- NO_PROXY 直连豁免（反代理劫持）


class TestDashscopeDirect:
    def test_appends_all_plan_hosts(self, monkeypatch):
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        llm_mod.ensure_dashscope_direct()
        merged = os.environ["NO_PROXY"]
        for host in llm_mod._DASHSCOPE_HOSTS:
            assert host in merged.split(","), merged

    def test_requests_bypasses_dashscope_but_not_others(self, monkeypatch):
        """有系统代理时：百炼域名豁免（直连），无关域名仍走代理。"""
        import requests.utils as ru

        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        llm_mod.ensure_dashscope_direct()
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        assert ru.should_bypass_proxies("https://dashscope.aliyuncs.com",
                                        no_proxy=None) is True
        assert ru.should_bypass_proxies(
            "https://coding.dashscope.aliyuncs.com", no_proxy=None) is True
        assert ru.should_bypass_proxies(
            "https://token-plan.cn-beijing.maas.aliyuncs.com", no_proxy=None) is True
        assert ru.should_bypass_proxies("https://example.com",
                                        no_proxy=None) is False
        assert ru.get_environ_proxies("https://example.com") != {}

    def test_idempotent_and_preserves_existing(self, monkeypatch):
        """已有 NO_PROXY 追加不覆盖、重复调用不膨胀。

        注：Windows 上 os.environ 键不区分大小写（NO_PROXY == no_proxy），
        故不单独操作小写键；大小写双写在 Linux 上由实现保证。
        """
        monkeypatch.setenv("NO_PROXY", "example.com")
        llm_mod.ensure_dashscope_direct()
        once = os.environ["NO_PROXY"]
        assert once.startswith("example.com")
        llm_mod.ensure_dashscope_direct()
        assert os.environ["NO_PROXY"] == once
        assert once.split(",").count("dashscope.aliyuncs.com") == 1

    def test_applied_on_import(self):
        """llm 模块 import 即生效（模块级调用），全宿主（web/cli/mcp）免配置。"""
        for host in llm_mod._DASHSCOPE_HOSTS:
            assert host in os.environ.get("NO_PROXY", "")
