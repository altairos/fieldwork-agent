"""fieldwork_agent 能力内核（无状态）。

依赖方向单向：kernel 只依赖标准库与 pydantic，不 import core/、cli、mcp、fastapi。
"""
from fieldwork_agent.kernel.registry import build_tool_schemas
from fieldwork_agent.kernel.registry import capabilities as CAPABILITIES

__all__ = ["CAPABILITIES", "build_tool_schemas"]
