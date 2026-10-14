"""聚合子包 — 由原子搜索原语组合出能力级产出。

依赖方向仍为单向：只依赖标准库、pydantic 与 ``kernel`` 内部模块，
不 import ``core/``、cli、mcp、fastapi。能力函数严格无状态：
``client`` 经参数注入，不读写任何项目状态。
"""
