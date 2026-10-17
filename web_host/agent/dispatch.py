"""能力分派：复用 kernel registry，同步 dispatch（loop 用 to_thread 包装）。"""
import inspect
from collections.abc import Callable
from typing import Any

from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.kernel.registry import capabilities, validate_tool_args


def build_dispatch_fn(flyai_client: Any, llm: Any,
                      live_snapshot_refs: dict[str, str] | None = None,
                      ) -> Callable[[str, dict], Envelope]:
    """live_snapshot_refs：目的地→快照引用串 的活字典（spec §5.1）；
    目标 fn 声明 snapshot_refs 形参时注入其当前内容浅拷贝——同一回合内
    先搜索后组合的调用链据此拿到刚落盘的快照引用。"""
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
        if live_snapshot_refs is not None and "snapshot_refs" in params:
            kwargs["snapshot_refs"] = dict(live_snapshot_refs)
        try:
            result = meta.fn(**kwargs)
        except Exception as exc:
            return Envelope(ok=False, error=ToolError(
                code="internal_error", message=str(exc)))
        return result if isinstance(result, Envelope) else Envelope(ok=True, data=result)

    return dispatch
