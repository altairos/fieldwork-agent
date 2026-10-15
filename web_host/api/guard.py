"""HTTP 侧 project_id 守卫接入点 — 单一定义，路由不得复制粘贴第二份。

规则语义单一来源在 ``kernel/repository/guard.py``（终审 C1，两个宿主薄壳共同依赖，
web_host 不 import mcp_server——宿主之间不互相依赖）；本模块只负责把拒绝原因折成
HTTP 400：消息中文、可含被拒的 id 与原因、**不泄露文件系统路径**。

前提钉住（guard.py 模块 docstring）：Starlette 在进 handler 前已解码 path 参数
（``..%2F`` 到手已是 ``../``），body/query 字段经 pydantic/Starlette 解析后同样是
Python 值——路由拿到的 project_id 已满足"已解码"入参前提，可直接送守卫。
"""

from typing import Any

from fastapi import HTTPException
from fieldwork_agent.kernel.repository.guard import PID_RULE_HINT, project_id_violation


def checked_pid(project_id: Any, repo: Any = None) -> str:
    """校验客户端可控的 project_id：非法 → raise 400；合法 → 原样返回供后续使用。

    所有 HTTP 入口必须在**触达 repo/TurnManager 之前**调用（TurnManager 同样以
    ``root / pid`` 拼 turns 目录路径）。``repo`` 用于 containment 双保险探测
    （``getattr(repo, "root", None)``，与 MCP 薄壳同款；没有 root 的替身只走字符校验）。
    """
    reason = project_id_violation(project_id, getattr(repo, "root", None))
    if reason:
        raise HTTPException(400, f"非法 project_id: {project_id!r}（{reason}）。{PID_RULE_HINT}")
    return project_id
