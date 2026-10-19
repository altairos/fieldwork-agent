# web_host/api/owner.py
"""归属解析单点（账号体系）：X-Owner-Token 裸值 → owner。

sess_ 令牌→查会话→user:<id>（失效抛 401 session_expired，R15）；其他→anon:<raw>。
三层凭据互不替代——本头只管归属，不管整站准入（X-App-Token）与模型调用（X-DashScope-Key 等）。
"""

from fastapi import HTTPException, Request
from fieldwork_agent.kernel.identity.owner_resolve import SessionExpired, resolve_owner
from fieldwork_agent.kernel.repository.guard import OWNER_TOKEN_RE
from fieldwork_agent.web_host.deps import get_user_store

OWNER_TOKEN_HEADER = "X-Owner-Token"


def owner_from_request(request: Request) -> str | None:
    raw = (request.headers.get(OWNER_TOKEN_HEADER) or "").strip()
    if not raw:
        return None
    if not OWNER_TOKEN_RE.fullmatch(raw):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_owner_token", "message": "非法 X-Owner-Token 头"})
    try:
        return resolve_owner(raw, get_user_store(request))
    except SessionExpired:
        raise HTTPException(
            status_code=401,
            detail={"code": "session_expired", "message": "登录已失效，请重新登录"}) from None
