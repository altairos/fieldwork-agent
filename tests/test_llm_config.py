"""kernel.llm + Config 模型三槽 — 全 mock，不联网、不需要真实 API Key。"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fieldwork_agent.core import config as config_mod
from fieldwork_agent.core.config import Config
from fieldwork_agent.kernel import llm as llm_mod
from fieldwork_agent.kernel.llm import LLMClient

# ---------------------------------------------------------------- Config 三槽


def _patch_dirs(monkeypatch, tmp_path):
    """R6：CONFIG_DIR / PROJECTS_DIR 同时指向 tmp_path 下不存在的路径，
    避免 _ensure_dirs() 在盘根 mkdir，Windows/CI 双平台安全。"""
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
