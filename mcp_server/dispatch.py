"""call_tool 分派：kernel 能力 → 统一包络 dict（spec §2.4；业务失败走包络不走协议异常）。

本模块不 import mcp SDK：只产 JSON 可序列化的 dict，由 server.py（Task 5）包装为
``CallToolResult``。包络形状统一为 ``{"ok", "data", "error": {code, message, hint}, "degraded"}``
（None 字段省略，见 ``envelope_dict``）。
"""
import inspect
import logging
from collections.abc import Mapping
from typing import Any

# capabilities 从 registry 直导（kernel/__init__ 导出的别名是 CAPABILITIES）
from fieldwork_agent.kernel.models import Envelope
from fieldwork_agent.kernel.registry import capabilities

# 终审⑥：INTERNAL 分支要可观测。库代码只取 logger，不配 handler/basicConfig（宿主决定落地）
logger = logging.getLogger(__name__)

UNKNOWN_TOOL = "unknown_tool"
INVALID_ARGS = "invalid_args"
INTERNAL = "internal_error"

# 保留键：不来自 MCP 客户端参数，只能由宿主 ctx 注入。
# - ``llm``        ：文档能力的生成器（kernel/documents），不注入即模板降级；
# - ``client``     ：搜索/聚合能力共享的 FlyAIClient（kernel/sources/flyai），不注入即自建；
# - ``amap_client``：地理六原语共享的 AMapClient（kernel/sources/amap）。
#   与 ``client`` 分道：无差别注入会把 FlyAIClient 塞给 amap 原语（AttributeError
#   → 假 internal_error），两种 client 类型不能共用一个通道。
RESERVED_KEYS: tuple[str, ...] = ("llm", "client", "amap_client")

# 分派表在模块级构建，测试可 monkeypatch 替换为替身（表内一律存 callable handler）
_CAPABILITY_FNS = {name: meta.fn for name, meta in capabilities().items()}
# 搜索原语的共享 FlyAIClient 版本 handler；由 server 组装时注入（Task 5）
_CLIENTLESS_SINGLETONS: dict[str, Any] = {}
# 组装期绑进单例 partial 的那个 client（server 写入，改造3）：请求级 ctx 携带
# 不同 client（动态 Header 重建）时须绕过单例，否则旧闭包吞掉新 key
_SINGLETON_CLIENT: Any = None

_Parameters = Mapping[str, inspect.Parameter]


def envelope_dict(env: Envelope) -> dict:
    """``Envelope`` → JSON 可序列化包络；None 字段整体省略，与 ``_error`` 形状对齐。"""
    return env.model_dump(mode="json", exclude_none=True)


def _error(code: str, message: str, hint: str = "") -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


def _declares(parameters: _Parameters, key: str) -> bool:
    """fn 是否**显式声明**了该保留键形参。

    只看具名形参：``**kwargs`` 容器不算"接受"——搜索原语的 ``**kwargs`` 会原样透传给
    ``FlyAIClient`` 的严格签名方法，塞进保留键只会把正常降级变成假 invalid_args。
    """
    param = parameters.get(key)
    return param is not None and param.kind is not inspect.Parameter.VAR_KEYWORD


def _resolve(name: str, ctx: dict | None = None) -> Any:
    """按名解析 handler：两张导入期快照优先，都未命中再回落查一次活注册表（终审⑤/M1）。

    ``_CAPABILITY_FNS`` 是 import 期快照，而 ``mapping.capability_tools()`` 每次活取注册表：
    将来若出现延迟注册，tools/list 会广告出快照里没有的名字，tools/call 却答 unknown_tool。
    回落 ``capabilities()`` 消除这层不对称；快照命中优先，monkeypatch 替身语义不变。

    改造3：单例 partial 绑死了组装期 client；请求级 ctx 携带**不同** client
    （动态 Header 重建）时跳过单例，回落 ``_CAPABILITY_FNS`` 走 ctx 注入路径。
    """
    req_client = (ctx or {}).get("client")
    bypass = (_SINGLETON_CLIENT is not None and req_client is not None
              and req_client is not _SINGLETON_CLIENT)
    fn = None if bypass else _CLIENTLESS_SINGLETONS.get(name)
    if fn is None:
        fn = _CAPABILITY_FNS.get(name)
    if fn is not None:
        return fn
    meta = capabilities().get(name)
    return meta.fn if meta is not None else None


def call_capability(name: str, arguments: dict, ctx: dict) -> dict:
    """按能力名分派并把结果折进统一包络；保留键按 ``fn`` 签名 + ctx 双条件注入。"""
    fn = _resolve(name, ctx)
    if fn is None:
        return _error(UNKNOWN_TOOL, f"未知工具: {name}",
                      "先 list_tools 获取可用工具名")
    try:
        # 终审③：签名探测单独成段——取不到签名（C 内建等）是服务端缺陷，
        # 与"客户端传错参数"无关，不得混进 INVALID_ARGS。
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError) as exc:
        logger.error("工具 %s 签名不可探测，折 internal_error 包络", name, exc_info=True)
        return _error(INTERNAL, f"无法探测工具签名: {exc}")
    try:
        injected = dict(arguments)   # 副本上动手，不污染调用方 dict
        for key in RESERVED_KEYS:
            # 信任边界（评审 Important#1）：先无条件剥离客户端可能塞入的同名值，
            # 再按双条件由 ctx 回填——确保 ``ctx`` 是保留键的唯一来源，与
            # ``RESERVED_KEYS`` 顶部注释承诺一致；否则 Task 5 直接透传原始
            # arguments 时，``{"client":"x"}`` 会让下游把字符串当 FlyAIClient 用。
            injected.pop(key, None)
            # 双条件：fn 声明了该保留键，且宿主 ctx 提供了它——
            # ctx 没有就不注入，让 fn 走自建默认（FlyAIClient() / 模板降级）。
            if key in ctx and _declares(parameters, key):
                injected[key] = ctx[key]
        result = fn(**injected)
    except (TypeError, ValueError) as exc:   # pydantic 校验与缺参/多参统一为 invalid_args
        return _error(INVALID_ARGS, f"参数校验失败: {exc}",
                      "对照该工具 inputSchema 修正参数")
    except Exception as exc:                 # 业务异常折进包络，保留消息（不吞错）
        logger.error("工具 %s 执行异常，折 internal_error 包络", name, exc_info=True)
        return _error(INTERNAL, f"执行异常: {exc}")
    if isinstance(result, Envelope):
        return envelope_dict(result)
    return {"ok": True, "data": result}
