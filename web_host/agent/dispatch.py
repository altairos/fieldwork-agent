"""能力分派：复用 kernel registry，同步 dispatch（loop 用 to_thread 包装）。"""
import inspect
from collections.abc import Callable
from typing import Any

from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.kernel.registry import capabilities
from pydantic import ValidationError


def build_dispatch_fn(flyai_client: Any, llm: Any) -> Callable[[str, dict], Envelope]:
    metas = capabilities()

    def dispatch(name: str, args: dict) -> Envelope:
        meta = metas.get(name)
        if meta is None:
            return Envelope(ok=False, error=ToolError(
                code="unknown_tool", message=f"未知工具: {name}"))
        try:
            validated = meta.args_model.model_validate(args).model_dump()
        except ValidationError as exc:
            msg = exc.errors()[0].get("msg", "") if exc.errors() else str(exc)
            return Envelope(ok=False, error=ToolError(
                code="invalid_arguments", message=f"参数校验失败：{msg}"))
        params = inspect.signature(meta.fn).parameters
        kwargs = dict(validated)
        if "client" in params:
            kwargs["client"] = flyai_client
        if "llm" in params:
            kwargs["llm"] = llm
        try:
            result = meta.fn(**kwargs)
        except Exception as exc:
            return Envelope(ok=False, error=ToolError(
                code="internal_error", message=str(exc)))
        return result if isinstance(result, Envelope) else Envelope(ok=True, data=result)

    return dispatch
