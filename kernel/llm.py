"""DashScope LLM 调用层 — 迁移自旧 ``tools/dashscope_tools.py``，支持 function calling。

约定：
- 构造无 api_key 或 SDK 缺失时抛 ``RuntimeError``（调用方以 ``Config.dashscope_available`` 预判）；
- ``models`` 为槽位→模型名映射（键 ``"interview"|"proposal"|"extract"``），本模块不 import Config；
- ``chat()`` 返回恒含 ``{"content", "tool_calls", "finish_reason", "usage"}`` 四键（成功与错误
  路径皆然，消费方可直读任意键），``tool_calls`` 为 ``[{"id", "name", "arguments": dict}]`` 或 ``None``；
- HTTP 非 200 不抛异常，返回四键的零值形态并附 ``error`` 字符串（loop 无异常通道，错误须有去处）。
"""
import json
from typing import Any

try:
    from dashscope import Generation
    _DASHSCOPE_AVAILABLE = True
except ImportError:  # SDK 未安装时降级，构造时报错
    Generation = None
    _DASHSCOPE_AVAILABLE = False


def _field(obj: Any, key: str) -> Any:
    """dict 与普通对象统一取字段。"""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _parse_tool_calls(raw: Any) -> list[dict[str, Any]] | None:
    """把 SDK 返回的 tool_calls 归一化为 ``{"id","name","arguments":dict}`` 列表。

    容错：某条 ``arguments`` 非法 JSON 则丢弃该条；全部无效或为空返回 ``None``。
    """
    if not raw:
        return None
    parsed: list[dict[str, Any]] = []
    for call in raw:
        fn = _field(call, "function")
        if fn is None:
            continue
        args_raw = _field(fn, "arguments")
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw)
            except (json.JSONDecodeError, ValueError):
                continue  # 非法 JSON 字符串：丢弃该条调用
        elif isinstance(args_raw, dict):
            args = args_raw
        else:
            args = {}
        parsed.append({
            "id": _field(call, "id") or "",
            "name": _field(fn, "name") or "",
            "arguments": args,
        })
    return parsed or None


class LLMClient:
    """按槽位调用 DashScope 千问模型的无状态客户端。"""

    def __init__(self, api_key: str, models: dict[str, str]):
        if not _DASHSCOPE_AVAILABLE:
            raise RuntimeError("DashScope SDK not installed. Run: pip install -U dashscope")
        if not api_key:
            raise RuntimeError("LLMClient 需要 api_key（调用方应先检查 Config.dashscope_available）")
        self.api_key = api_key
        self.models = models

    def chat(self, messages: list[dict], *, slot: str = "proposal",
             tools: list[dict] | None = None, temperature: float = 0.7,
             max_tokens: int = 4096) -> dict[str, Any]:
        """发送对话请求；``slot`` 选模型槽位，``tools`` 非空时启用 function calling。"""
        call_kwargs: dict[str, Any] = {
            "model": self.models[slot],
            "messages": messages,
            "api_key": self.api_key,
            "result_format": "message",  # choices/message 结构的前提
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            call_kwargs["tools"] = tools
        response = Generation.call(**call_kwargs)
        if response.status_code != 200:
            # 契约四键恒在：消费方（宿主/loop）可无条件读 usage 等键，error 为附加错误通道。
            return {"content": "", "tool_calls": None, "finish_reason": "",
                    "usage": {}, "error": str(response.message)}
        choice = response.output.choices[0]
        message = choice.message
        return {
            "content": _field(message, "content") or "",
            "tool_calls": _parse_tool_calls(_field(message, "tool_calls")),
            "finish_reason": _field(choice, "finish_reason") or "",
            "usage": response.usage,
        }
