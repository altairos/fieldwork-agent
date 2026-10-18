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


def checked_owned_project(project_id: str, repo: Any, owner: str | None) -> dict:
    """归属守卫（spec §端点矩阵）：非法 pid 400；归属不符/项目无主/
    无令牌/不存在一律 404，不泄露项目存在性（他人项目与不存在同形）。

    通过时返回项目快照（省去端点二次 get_state）；仅作守卫用途的
    调用方可忽略返回值。owner 为 None（未携带令牌）时任何项目不可见
    ——POST /projects 的 400 owner_token_required 与 GET /projects 的
    空列表由端点自行处理，不走本守卫。
    """
    checked_project_id(project_id, repo)
    try:
        state = repo.get_state(project_id)
    except KeyError:
        state = None
    if owner is None or state is None or state.get("owner") != owner:
        raise HTTPException(404, f"项目不存在: {project_id}")
    return state
