"""能力分派：复用 kernel registry，同步 dispatch（loop 用 to_thread 包装）。"""
import inspect
from collections.abc import Callable
from typing import Any

from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.kernel.registry import capabilities, validate_tool_args


def build_dispatch_fn(flyai_client: Any, llm: Any,
                      amap_client: Any = None) -> Callable[[str, dict], Envelope]:
    """能力分派（R08 §6）：kernel 聚合产出携带规格键（``@live:工具:参数``），
    宿主轮末统一替换为真实快照引用——dispatch 不再注入 snapshot_refs。"""
    metas = capabilities()

    def dispatch(name: str, args: dict) -> Envelope:
        meta = metas.get(name)
        if meta is None:
            return Envelope(ok=False, error=ToolError(
                code="unknown_tool", message=f"未知工具: {name}"))
        validated, err_msg = validate_tool_args(meta, args)
        if validated is None:
            return Envelope(ok=False, error=ToolError(
                code="invalid_arguments", message=f"参数校验失败：{err_msg}"))
        params = inspect.signature(meta.fn).parameters
        kwargs = dict(validated)
        if "client" in params:
            kwargs["client"] = flyai_client
        if "llm" in params:
            kwargs["llm"] = llm
        # 与 client 分道防串味（同 CLI/MCP 惯例）；None 注入等价自建降级
        if "amap_client" in params:
            kwargs["amap_client"] = amap_client
        try:
            result = meta.fn(**kwargs)
        except Exception as exc:
            return Envelope(ok=False, error=ToolError(
                code="internal_error", message=str(exc)))
        return result if isinstance(result, Envelope) else Envelope(ok=True, data=result)

    return dispatch
