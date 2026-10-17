"""DashScope LLM 调用层 — 迁移自旧 ``tools/dashscope_tools.py``，支持 function calling。

约定：
- 构造无 api_key 或 SDK 缺失时抛 ``RuntimeError``（调用方以 ``Config.dashscope_available`` 预判）；
- ``models`` 为槽位→模型名映射（键 ``"interview"|"proposal"|"extract"``），本模块不 import Config；
- ``chat()`` 返回恒含 ``{"content", "tool_calls", "finish_reason", "usage"}`` 四键（成功与错误
  路径皆然，消费方可直读任意键），``tool_calls`` 为 ``[{"id", "name", "arguments": dict}]`` 或 ``None``；
- HTTP 非 200 不抛异常，返回四键的零值形态并附 ``error`` 字符串（loop 无异常通道，错误须有去处）。

套餐 Key（``sk-sp-``）：百炼 Coding/Token 套餐与按量付费凭证不互通，须各配专属 OpenAI
兼容端点。``base_url`` 非空时本模块以 httpx 直连 ``{base_url}/chat/completions``（OpenAI
协议，messages/tools 形状同构无需转换），契约与 SDK 原生路径完全一致，消费方（loop）无感
切换；端点取值仅限 ``PLAN_BASE_URLS`` 白名单（``plan_base_url()``），杜绝对任意 URL 直连。
"""
import json
from typing import Any

try:
    from dashscope import Generation
    _DASHSCOPE_AVAILABLE = True
except ImportError:  # SDK 未安装时降级，构造时报错
    Generation = None
    _DASHSCOPE_AVAILABLE = False

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:  # 套餐端点（base_url）依赖 httpx，缺失时构造报错
    httpx = None
    _HTTPX_AVAILABLE = False

# 百炼套餐专属 OpenAI 兼容端点（白名单：套餐名 → base_url）。
PLAN_BASE_URLS: dict[str, str] = {
    "coding": "https://coding.dashscope.aliyuncs.com/v1",
    "token": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
}


def plan_base_url(plan: str | None) -> str | None:
    """套餐类型 → 白名单端点；空/default/未知/任意 URL → ``None``（走 SDK 原生端点）。"""
    return PLAN_BASE_URLS.get((plan or "").strip().lower())


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


def _empty_result(error: str) -> dict[str, Any]:
    """错误路径的四键零值形态（与 SDK 原生路径错误契约一致）。"""
    return {"content": "", "tool_calls": None, "finish_reason": "", "usage": {}, "error": error}


def _compat_error_message(resp: Any) -> str:
    """OpenAI 兼容端点错误响应 → 可读 message（error.message 优先，text 片段兕底）。"""
    try:
        data = resp.json()
    except Exception:
        data = None
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str) and err:
            return err
    text = (getattr(resp, "text", "") or "").strip()
    return text[:200] or f"HTTP {resp.status_code}"


def _compat_timeout() -> Any:
    """套餐端点超时：连接 10s / 读 180s（SSE 长流）/ 写 30s。"""
    return httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


class LLMClient:
    """按槽位调用 DashScope 千问模型的无状态客户端。"""

    # 类属性兑底：支持 ``__new__`` 直构（测试/序列化场景）不经 ``__init__``。
    base_url: str | None = None

    def __init__(self, api_key: str, models: dict[str, str], base_url: str | None = None):
        if base_url:
            if not _HTTPX_AVAILABLE:
                raise RuntimeError("base_url（OpenAI 兼容端点）需要 httpx. Run: pip install -U httpx")
        elif not _DASHSCOPE_AVAILABLE:
            raise RuntimeError("DashScope SDK not installed. Run: pip install -U dashscope")
        if not api_key:
            raise RuntimeError("LLMClient 需要 api_key（调用方应先检查 Config.dashscope_available）")
        self.api_key = api_key
        self.models = models
        self.base_url = base_url or None

    def chat(self, messages: list[dict], *, slot: str = "proposal",
             tools: list[dict] | None = None, temperature: float = 0.7,
             max_tokens: int = 4096) -> dict[str, Any]:
        """发送对话请求；``slot`` 选模型槽位，``tools`` 非空时启用 function calling。"""
        if self.base_url:
            return self._compat_chat(messages, slot=slot, tools=tools,
                                     temperature=temperature, max_tokens=max_tokens)
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

    def chat_stream(self, messages: list[dict], *, slot: str = "interview",
                    tools: list[dict] | None = None, temperature: float = 0.7,
                    max_tokens: int = 4096):
        """流式对话：逐 token yield {"delta": str}，最终 yield 四键终止帧。

        DashScope stream=True + incremental_output=True：每 chunk 只含增量文本。
        tool_calls 在流式中仅出现在最终 chunk（DashScope 行为），故终止帧统一解析。
        """
        if self.base_url:
            yield from self._compat_chat_stream(messages, slot=slot, tools=tools,
                                                temperature=temperature, max_tokens=max_tokens)
            return
        call_kwargs: dict[str, Any] = {
            "model": self.models[slot], "messages": messages,
            "api_key": self.api_key, "result_format": "message",
            "temperature": temperature, "max_tokens": max_tokens,
            "stream": True, "incremental_output": True,
        }
        if tools:
            call_kwargs["tools"] = tools
        accumulated = ""
        final_tool_calls = None
        finish_reason = ""
        usage: dict = {}
        for response in Generation.call(**call_kwargs):
            if response.status_code != 200:
                yield {"content": "", "tool_calls": None, "finish_reason": "",
                       "usage": {}, "error": str(response.message)}
                return
            choice = response.output.choices[0]
            message = choice.message
            delta = _field(message, "content") or ""
            if delta:
                accumulated += delta
                yield {"delta": delta}
            tc = _parse_tool_calls(_field(message, "tool_calls"))
            if tc:
                final_tool_calls = tc
            fr = _field(choice, "finish_reason") or ""
            if fr and fr != "null":
                finish_reason = fr
            if response.usage:
                usage = response.usage
        yield {"content": accumulated, "tool_calls": final_tool_calls,
               "finish_reason": finish_reason, "usage": usage}

    # ---------------------------------------------------- OpenAI 兼容（套餐端点）

    def _compat_request(self, messages: list[dict], *, slot: str,
                        tools: list[dict] | None, temperature: float, max_tokens: int,
                        stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.models[slot], "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _compat_chat(self, messages: list[dict], *, slot: str,
                     tools: list[dict] | None, temperature: float,
                     max_tokens: int) -> dict[str, Any]:
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=self._compat_request(messages, slot=slot, tools=tools,
                                          temperature=temperature, max_tokens=max_tokens,
                                          stream=False),
                timeout=_compat_timeout())
        except httpx.HTTPError as exc:
            return _empty_result(f"网络错误：{exc}")
        if resp.status_code != 200:
            return _empty_result(_compat_error_message(resp))
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return {
            "content": message.get("content") or "",
            "tool_calls": _parse_tool_calls(message.get("tool_calls")),
            "finish_reason": choice.get("finish_reason") or "",
            "usage": data.get("usage") or {},
        }

    def _compat_chat_stream(self, messages: list[dict], *, slot: str,
                            tools: list[dict] | None, temperature: float, max_tokens: int):
        try:
            with httpx.stream(
                "POST", f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=self._compat_request(messages, slot=slot, tools=tools,
                                          temperature=temperature, max_tokens=max_tokens,
                                          stream=True),
                timeout=_compat_timeout()) as resp:
                if resp.status_code != 200:
                    # 流式响应须先读全 body 再取 .json()/.text，否则抛
                    # ResponseNotRead（RuntimeError 子类）掩盖真实 API 错误消息
                    resp.read()
                    yield _empty_result(_compat_error_message(resp))
                    return
                accumulated = ""
                tool_parts: dict[int, dict[str, str]] = {}
                finish_reason = ""
                usage: dict = {}
                for line in resp.iter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue  # 空行/心跳注释行
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except (json.JSONDecodeError, ValueError):
                        continue  # 坏行容错
                    if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    if text:
                        accumulated += text
                        yield {"delta": text}
                    # tool_calls 跨 chunk 增量：按 index 合并 id/name 与 arguments 片段
                    for piece in delta.get("tool_calls") or []:
                        idx = piece.get("index", 0)
                        part = tool_parts.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""})
                        if piece.get("id"):
                            part["id"] = piece["id"]
                        fn = piece.get("function") or {}
                        if fn.get("name"):
                            part["name"] = fn["name"]
                        if fn.get("arguments"):
                            part["arguments"] += fn["arguments"]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                final_calls = None
                if tool_parts:
                    final_calls = []
                    for idx in sorted(tool_parts):
                        part = tool_parts[idx]
                        try:
                            args = json.loads(part["arguments"] or "{}")
                        except (json.JSONDecodeError, ValueError):
                            args = {}
                        final_calls.append({"id": part["id"], "name": part["name"],
                                            "arguments": args})
                yield {"content": accumulated, "tool_calls": final_calls,
                       "finish_reason": finish_reason, "usage": usage}
        except httpx.HTTPError as exc:
            yield _empty_result(f"网络错误：{exc}")
