"""外部数据源适配器（sources）。

依赖方向仍为单向：只依赖标准库、pydantic 与 ``kernel.models``，
不 import ``core/``、cli、mcp、fastapi。
"""
