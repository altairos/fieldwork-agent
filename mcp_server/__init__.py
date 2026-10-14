"""MCP Server 宿主层（薄壳）：把 kernel 能力面暴露为 MCP tools/resources/prompts。

依赖方向单向：本包可 import `kernel/` 与 `core.config`，kernel 不得反向依赖 mcp SDK。
"""
