# web_host/api/owner.py
"""匿名归属令牌解析（spec §归属令牌）：X-Owner-Token 裸值 → anon:<token>。

账号体系四单点之二：未来接入账号体系时只改本模块（把 user_id 映射为
owner 值），端点与守卫零改动。三层凭据互不替代——本头只管归属，
不管整站准入（X-App-Token）与模型调用（X-DashScope-Key / X-FlyAI-Key）。
"""

import re

from fastapi import HTTPException, Request

OWNER_TOKEN_HEADER = "X-Owner-Token"
OWNER_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def owner_from_request(request: Request) -> str | None:
    """X-Owner-Token → 归一化归属值（``anon:<raw>``）。

    头缺失或纯空白 → None（无令牌，后续行为由端点矩阵决定：POST
    /projects 400、GET /projects 空列表、其余 /projects/* 一律 404）；
    形状非法 → 400 invalid_owner_token。``anon:`` 前缀只由服务端拼接，
    客户端永不携带。
    """
    raw = (request.headers.get(OWNER_TOKEN_HEADER) or "").strip()
    if not raw:
        return None
    if not OWNER_TOKEN_RE.fullmatch(raw):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_owner_token", "message": "非法 X-Owner-Token 头"},
        )
    return f"anon:{raw}"
