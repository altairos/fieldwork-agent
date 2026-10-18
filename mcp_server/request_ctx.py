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

# None = 无覆盖（回落组装期 env 兜底）；空 dict 同样视为无覆盖
_OVERRIDES: ContextVar[dict[str, str] | None] = ContextVar(
    "fieldwork_request_overrides", default=None
)


def overrides() -> dict[str, str]:
    """当前请求的覆盖映射；无覆盖返回空 dict（调用方判空即可）。"""
    return dict(_OVERRIDES.get() or {})


def set_overrides(mapping: dict[str, str]):
    """写入本请求覆盖，返回 reset 用的 token。"""
    return _OVERRIDES.set(dict(mapping))


def reset(token) -> None:
    """请求结束复位（中间件 finally 保证执行）。"""
    _OVERRIDES.reset(token)
