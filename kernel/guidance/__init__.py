"""引导策略子包（阶段/提取/访谈对象库）。

依赖方向仍为单向：只依赖标准库、pydantic 与 ``kernel.models``，
不 import ``core/``、cli、mcp、fastapi；阶段推进判定为确定性纯函数，不含 LLM。
"""
