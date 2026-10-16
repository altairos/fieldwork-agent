"""持久化子包（repository）— Repository Protocol 是稳定边界（spec §5.1）。

依赖方向单向：只依赖标准库与 ``kernel`` 内部常量，不 import ``core/``、cli、mcp、fastapi。

约定（spec 公理 1）：kernel 能力函数**不碰本包**，落盘一律由宿主显式调用；
本包只做状态读写，阶段推进语义归 ``kernel.guidance``。
"""
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository

__all__ = ["SqliteRepository"]
