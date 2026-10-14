"""文档生成子包（documents）— kernel 内**唯一 LLM 消费者**（spec 公理 2）。

依赖方向仍为单向：只依赖标准库、pydantic 与 ``kernel`` 内部模块，
不 import ``core/``、cli、mcp、fastapi。能力函数无状态：``llm`` 经 keyword-only
参数注入，``None`` 时走模板降级路径，输出 markdown 保持结构一致可用。
"""
