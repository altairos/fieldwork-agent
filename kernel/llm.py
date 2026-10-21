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
import os
import time
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

# 默认端点（SDK 原生）+ 两套餐端点，NO_PROXY 直连豁免清单。
_DASHSCOPE_HOSTS: tuple[str, ...] = (
    "dashscope.aliyuncs.com",
    "coding.dashscope.aliyuncs.com",
    "token-plan.cn-beijing.maas.aliyuncs.com",
)


def ensure_dashscope_direct() -> None:
    """把百炼域名并入进程级 ``NO_PROXY``（幂等，不覆盖已有配置）。

    反代理劫持：Windows 上 dashscope SDK 底层的 requests 除环境变量外还读
    注册表系统代理（本机 clash 类代理对百炼转发失败时，SDK 仅报 "url error"
    掩盖真实原因）；百炼为国内服务直连最优。requests/httpx 均尊重
    ``NO_PROXY``，模块 import 即调用，全宿主（web/cli/mcp）免配置。
    """
    cur = os.environ.get("NO_PROXY", os.environ.get("no_proxy", ""))
    parts = [p.strip() for p in cur.split(",") if p.strip()]
    for host in _DASHSCOPE_HOSTS:
        if not any(p == host or p.endswith("." + host) for p in parts):
            parts.append(host)
    merged = ",".join(parts)
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


ensure_dashscope_direct()


def plan_base_url(plan: str | None) -> str | None:
    """套餐类型 → 白名单端点；空/default/未知/任意 URL → ``None``（走 SDK 原生端点）。"""
    return PLAN_BASE_URLS.get((plan or "").strip().lower())


def _wants_thinking_off(model: str) -> bool:
    """qwen3 系混合思考模型（qwen3.8/3.7/3.6/3.5，默认开思考）才需显式
    ``enable_thinking=False``：思考流走 ``reasoning_content``、正文走
    ``content``，本层只解析 ``content``，开着思考会出现工具结果回填后的
    最终轮「只想不写」→ 正文为空，回合无回复即结束。门控边界：仅思考模型
    （``-thinking`` 后缀，只接受 true）与非思考模型（qwen-max 等）传该参数
    会被网关 400 NotSupportEnableThinking 拒绝，不能无条件传。
    """
    name = (model or "").lower()
    return name.startswith("qwen3") and "-thinking" not in name


# 默认端点的 OpenAI 兼容入口：所有对话模型统一路由，不按 aigc 服务路径区分，
# 作为 SDK 原生路径週 400 "url error" 时的回退（内部常量，非用户输入）。
_DEFAULT_COMPAT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _is_gateway_url_error(response: Any) -> bool:
    """百炼网关的「模型不在该 aigc 服务路径」信号。

    网关把每个模型绑定到唯一服务路径（text/multimodal-generation），SDK 原生
    调用固定打 text-generation；模型实际在其它路径时网关报 400 "url error"
    ——消息文本误导，实为路径不匹配（见 dashscope SDK acli/providers/tongyi.py
    的同款处理：双路径探测）。判定不限定状态码（SDK 包装形态未必稳定），
    以 "url error" 特征串为准；仅在非 200 分支调用，误判回退到统一路由的
    兼容端点也无害（其自有错误通道会给出真实原因）。
    """
    haystack = f"{getattr(response, 'code', '')} {getattr(response, 'message', '')}"
    return "url error" in haystack.lower()


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


# ---------------------------------------------------------------- 瞬时故障与分类

# 百炼链路的「连接被对端掐断 / 502-504」多为网关抖动，一次短退避重试即可救回；
# 流式仅在**尚未吐出任何正文增量**时重试（已吐字再重试会重复文本）。
_LLM_MAX_ATTEMPTS = 2
_LLM_RETRY_DELAY_S = 0.6
_RETRYABLE_STATUS = frozenset({502, 503, 504})

try:  # dashscope SDK 底层用 requests；连接级异常可与 httpx 一并视为瞬时故障
    from requests.exceptions import RequestException as _RequestsRequestError
except ImportError:  # pragma: no cover —— dashscope 依赖 requests，正常环境不会缺
    _RequestsRequestError = None

_TRANSPORT_ERRORS = tuple(
    t for t in (_RequestsRequestError, httpx.HTTPError if _HTTPX_AVAILABLE else None)
    if t is not None)

# 错误分类特征串（小写匹配，顺序即优先级：额度 → 端点 → 凭证 → 网络）。
# 分类结果供宿主生成**对症**的 hint；此前所有非审核错误统一提示「检查 API Key」，
# 额度耗尽/网络中断/端点配错时会把用户引到错误方向。
_QUOTA_MARKERS = ("free quota exhausted", "quota exceeded", "insufficient quota",
                  "insufficient_quota", "arrearage", "额度", "欠费")
_URL_MARKERS = ("url error", "invalid url", "unsupported url")
_AUTH_MARKERS = ("invalid api-key", "invalid api key", "invalid access token",
                 "token expired", "incorrect api key", "invalidaccesskeyid",
                 "unauthorized")
_NETWORK_MARKERS = ("网络错误", "peer closed", "connection reset", "connection aborted",
                    "connection refused", "remote end closed", "incomplete chunked read",
                    "timed out", "timeout", "name resolution", "name or service not known",
                    "ssl", "proxy")

# 输入超长（P1-11）：百炼/兼容端点与 OpenAI 的报错口径不一，统一在这里认。
# 命中即说明"重试也没用"——只能缩提示或换长上下文模型，必须单独上报原因。
_INPUT_TOO_LONG_MARKERS = (
    "range of input length", "input length", "input is too long",
    "maximum context length", "context length exceeded", "context_length_exceeded",
    "too many tokens", "prompt is too long", "string_above_max_length",
    "input too long", "输入过长", "超出模型长度限制", "内容过长",
)


def classify_llm_error(error: object) -> str:
    """LLM 错误串 → ``quota|base_url|auth|network|input_too_long|unknown``
    （None/空串安全）。

    只做**传输/账户层**分类，外加一个高优先级特例：``input_too_long``
    （输入超出模型长度，属请求负载层，重试/充值都无用）。
    内容审核走 ``is_content_moderation_error``（宿主先判它）。
    输入超长优先级最高：超长报错可能同时带 quota 字样，但重试/充值都无用，
    必须单独上报（P2-a）。未命中的错误回落 ``unknown``——宁可提示含糊，也不臆造原因。
    """
    text = str(error or "").lower()
    if not text:
        return "unknown"
    if is_input_too_long(text):
        return "input_too_long"
    for kind, markers in (("quota", _QUOTA_MARKERS), ("base_url", _URL_MARKERS),
                          ("auth", _AUTH_MARKERS), ("network", _NETWORK_MARKERS)):
        if any(m in text for m in markers):
            return kind
    return "unknown"


def is_input_too_long(error: object) -> bool:
    """错误串是否为「输入超出模型长度限制」（P1-11）。

    这类失败与网络/额度不同：重试无用、降级也不是"模型暂时不可用"，
    调用方应把原因如实标成 ``input_too_long``（而不是模糊的"调用失败"）。
    """
    text = str(error or "").lower()
    return bool(text) and any(m in text for m in _INPUT_TOO_LONG_MARKERS)


def _sleep_before_retry() -> None:
    """重试前短退避（测试可把 ``_LLM_RETRY_DELAY_S`` 置 0 免等）。"""
    if _LLM_RETRY_DELAY_S > 0:
        time.sleep(_LLM_RETRY_DELAY_S)


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
        if _wants_thinking_off(self.models[slot]):
            call_kwargs["enable_thinking"] = False
        for attempt in range(_LLM_MAX_ATTEMPTS):
            last = attempt == _LLM_MAX_ATTEMPTS - 1
            try:
                response = Generation.call(**call_kwargs)
            except _TRANSPORT_ERRORS as exc:   # SDK 路径的连接级抖动：一次退避重试
                if not last:
                    _sleep_before_retry()
                    continue
                return _empty_result(f"网络错误：{exc}")
            if response.status_code != 200:
                # 契约四键恒在：消费方（宿主/loop）可无条件读 usage 等键；error 为附加错误通道。
                if _is_gateway_url_error(response) and _HTTPX_AVAILABLE:
                    return self._compat_chat(messages, slot=slot, tools=tools,
                                             temperature=temperature, max_tokens=max_tokens,
                                             base_url=_DEFAULT_COMPAT_BASE)
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
        return _empty_result("网络错误：重试耗尽")      # 防御性返回，正常路径不可达

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
        if _wants_thinking_off(self.models[slot]):
            call_kwargs["enable_thinking"] = False
        for attempt in range(_LLM_MAX_ATTEMPTS):
            last = attempt == _LLM_MAX_ATTEMPTS - 1
            accumulated = ""
            final_tool_calls = None
            finish_reason = ""
            usage: dict = {}
            try:
                for response in Generation.call(**call_kwargs):
                    if response.status_code != 200:
                        if (_is_gateway_url_error(response) and _HTTPX_AVAILABLE
                                and not accumulated):
                            # url error 必发于首 chunk（网关路由拒绝）；已输出过内容则不重试，
                            # 避免兼容端点重复吐同样的增量。
                            yield from self._compat_chat_stream(
                                messages, slot=slot, tools=tools,
                                temperature=temperature, max_tokens=max_tokens,
                                base_url=_DEFAULT_COMPAT_BASE)
                            return
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
                return
            except _TRANSPORT_ERRORS as exc:
                # 已吐字绝不重试（会重复正文）；未吐字时允许一次短退避重试
                if not last and not accumulated:
                    _sleep_before_retry()
                    continue
                yield _empty_result(f"网络错误：{exc}")
                return

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
        if _wants_thinking_off(self.models[slot]):
            payload["enable_thinking"] = False
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _compat_chat(self, messages: list[dict], *, slot: str,
                     tools: list[dict] | None, temperature: float,
                     max_tokens: int, base_url: str | None = None) -> dict[str, Any]:
        url_base = base_url or self.base_url
        payload = self._compat_request(messages, slot=slot, tools=tools,
                                       temperature=temperature, max_tokens=max_tokens,
                                       stream=False)
        for attempt in range(_LLM_MAX_ATTEMPTS):
            last = attempt == _LLM_MAX_ATTEMPTS - 1
            try:
                resp = httpx.post(
                    f"{url_base}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload, timeout=_compat_timeout())
            except httpx.HTTPError as exc:
                if not last:                      # 连接级抖动：一次短退避重试
                    _sleep_before_retry()
                    continue
                return _empty_result(f"网络错误：{exc}")
            if resp.status_code in _RETRYABLE_STATUS and not last:
                _sleep_before_retry()             # 网关 502/503/504：同属瞬时故障
                continue
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
        return _empty_result("网络错误：重试耗尽")      # 防御性返回，正常路径不可达

    def _compat_chat_stream(self, messages: list[dict], *, slot: str,
                            tools: list[dict] | None, temperature: float, max_tokens: int,
                            base_url: str | None = None):
        url_base = base_url or self.base_url
        payload = self._compat_request(messages, slot=slot, tools=tools,
                                       temperature=temperature, max_tokens=max_tokens,
                                       stream=True)
        for attempt in range(_LLM_MAX_ATTEMPTS):
            last = attempt == _LLM_MAX_ATTEMPTS - 1
            emitted = False          # 本次尝试是否已向调用方吐出正文增量
            try:
                with httpx.stream(
                    "POST", f"{url_base}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=_compat_timeout()) as resp:
                    if resp.status_code != 200:
                        # 流式响应须先读全 body 再取 .json()/.text，否则抛
                        # ResponseNotRead（RuntimeError 子类）掩盖真实 API 错误消息
                        resp.read()
                        if resp.status_code in _RETRYABLE_STATUS and not last:
                            _sleep_before_retry()
                            continue
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
                            emitted = True
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
                    return
            except httpx.HTTPError as exc:
                # 已吐字绝不重试（会重复正文）；未吐字时允许一次短退避重试
                if not last and not emitted:
                    _sleep_before_retry()
                    continue
                yield _empty_result(f"网络错误：{exc}")
                return
