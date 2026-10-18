# web_host/api/owner.py
"""匿名归属令牌解析（spec §归属令牌）：X-Owner-Token 裸值 → anon:<token>。

账号体系四单点之二：本模块是 REST 入口的 FastAPI 适配层（头名 + HTTPException
折叠）；令牌→owner 的归一化规则已下沉 kernel/repository/guard.normalize_owner_token，
与 MCP HTTP 入口（mcp_server.http_entry）共用同一来源。三层凭据互不替代——
本头只管归属，不管整站准入（X-App-Token）与模型调用（X-DashScope-Key / X-FlyAI-Key）。
"""

from fastapi import HTTPException, Request
from fieldwork_agent.kernel.repository.guard import normalize_owner_token

OWNER_TOKEN_HEADER = "X-Owner-Token"


def owner_from_request(request: Request) -> str | None:
    """X-Owner-Token → 归一化归属值（``anon:<raw>``）。

    头缺失或纯空白 → None（无令牌，后续行为由端点矩阵决定：POST
    /projects 400、GET /projects 空列表、其余 /projects/* 一律 404）；
    形状非法 → 400 invalid_owner_token。``anon:`` 前缀只由服务端拼接，
    客户端永不携带（归一化规则单一来源：kernel guard.normalize_owner_token）。
    """
    raw = (request.headers.get(OWNER_TOKEN_HEADER) or "").strip()
    try:
        return normalize_owner_token(raw)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_owner_token", "message": "非法 X-Owner-Token 头"},
        ) from None
