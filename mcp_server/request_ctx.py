"""请求级动态配置覆盖（改造3：Header → contextvar → 请求级 host_ctx 重建）。

百炼 Managed Agents 每次 MCP 调用会代传动态 Header（X-Flyai-Key /
X-Dashscope-Key / X-Amap-Key）；纯 ASGI 中间件在请求进入时写入 contextvar，随后同一
async 任务链上的工具调用（含 anyio.to_thread 工作线程）都能读到，
请求结束复位，天然逐请求隔离、互不串号。
"""
from contextvars import ContextVar

DASHSCOPE_KEY_HEADER = "x-dashscope-key"
FLYAI_KEY_HEADER = "x-flyai-key"
AMAP_KEY_HEADER = "x-amap-key"
# N01：归属令牌头（ASGI 恒小写）。与 REST 侧 web_host.api.owner.OWNER_TOKEN_HEADER
# 同名同义，仅大小写约定随框架而异（同现有供应商头的处理方式）。
OWNER_TOKEN_HEADER = "x-owner-token"

# None = 无覆盖（回落组装期 env 兜底）；空 dict 同样视为无覆盖
_OVERRIDES: ContextVar[dict[str, str] | None] = ContextVar(
    "fieldwork_request_overrides", default=None
)

# N01：请求级归属身份（已归一化的 ``anon:<raw>`` 或 None）。
# 仅在共享 HTTP 模式由中间件按请求 set/reset；stdio 可信模式恒不 set（读回 _UNSET）。
# 用专属哨兵区分“未进入强制模式”（_UNSET）与“已解析但无令牌”（None）。
_UNSET = object()
_OWNER: ContextVar[object] = ContextVar("fieldwork_request_owner", default=_UNSET)


def owner() -> object:
    """当前请求的归属身份：``anon:<raw>`` / None（无令牌）/ ``_UNSET``（未强制）。"""
    return _OWNER.get()


def set_owner(value: str | None):
    """写入本请求归属身份，返回 reset 用的 token。"""
    return _OWNER.set(value)


def is_unset() -> bool:
    """归属哨兵判定（调用方据此区分可信全量模式与共享强制模式）。"""
    return _OWNER.get() is _UNSET


def resolved_owner() -> str | None:
    """强制模式下的归属值：``anon:<raw>`` 或 None。

    中间件未注入（_UNSET，理论上不发生于 HTTP 路径）按无令牌 None 处理，
    使强制模式恒得到可比较的具体值（str 或 None），不泄露哨兵对象。
    """
    val = _OWNER.get()
    return None if val is _UNSET else val


def overrides() -> dict[str, str]:
    """当前请求的覆盖映射；无覆盖返回空 dict（调用方判空即可）。"""
    return dict(_OVERRIDES.get() or {})


def set_overrides(mapping: dict[str, str]):
    """写入本请求覆盖，返回 reset 用的 token。"""
    return _OVERRIDES.set(dict(mapping))


def reset(token) -> None:
    """请求结束复位（中间件 finally 保证执行）。"""
    _OVERRIDES.reset(token)


def reset_owner(token) -> None:
    """归属身份复位（中间件 finally 保证执行）。"""
    _OWNER.reset(token)
