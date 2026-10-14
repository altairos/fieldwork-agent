"""运行时层（runtime）— CLI/Web 共用的宿主运行库。

依赖方向：``runtime`` 可 import ``kernel``，但 ``kernel`` 绝不 import ``runtime``
（kernel 只做无状态能力，禁止 LLM 编排；共享回合循环归本层）。
"""
