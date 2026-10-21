"""MCP Server 宿主层（薄壳）：把 kernel 能力面暴露为 MCP tools/resources/prompts。

依赖方向单向：本包可 import `kernel/`（A09 起不再依赖 core），kernel 不得反向依赖 mcp SDK。
"""
from fieldwork_agent.mcp_server.server import create_server

__all__ = ["create_server"]
