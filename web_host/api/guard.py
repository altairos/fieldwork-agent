"""Web 宿主的 ID 守卫薄壳：kernel 规则 → HTTPException 400。

审计 F01：错误消息不回显原始输入（浏览器反射面），与 MCP 侧（CLI 诊断、
保留回显）策略刻意不同。
"""

from typing import Any

from fastapi import HTTPException
from fieldwork_agent.kernel.repository.guard import (
    project_id_violation,
    turn_id_violation,
)


def checked_project_id(project_id: str, repo: Any = None) -> str:
    """非法 project_id → 400 invalid_project_id；合法原样返回。"""
    reason = project_id_violation(project_id, repo)
    if reason:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_project_id", "message": f"非法 project_id（{reason}）"},
        )
    return project_id


def checked_turn_id(turn_id: str) -> str:
    """非法 turn_id → 400 invalid_turn_id；合法原样返回。"""
    reason = turn_id_violation(turn_id)
    if reason:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_turn_id", "message": f"非法 turn_id（{reason}）"},
        )
    return turn_id
