"""kernel.llm + Config 模型三槽 — 全 mock，不联网、不需要真实 API Key。"""
import json

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
