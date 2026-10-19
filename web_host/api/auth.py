# web_host/api/auth.py
"""账号体系端点：注册/登录/登出/me + 登录认领（CAS，R2）。"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fieldwork_agent.web_host.api.owner import owner_from_request
from fieldwork_agent.web_host.deps import get_repo, get_user_store
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str
    password: str
    anon_token: str | None = None


def _claim(repo, user_owner: str, anon_token: str | None) -> dict:
    """anon:<token> 的项目逐个 CAS 迁到 user:<id>（R2）；不符则跳过不覆盖。"""
    if not anon_token:
        return {"claimed": 0, "skipped": 0}
    anon_owner = f"anon:{anon_token}"
    claimed = skipped = 0
    for p in repo.list_projects(owner=anon_owner):
        if repo.set_owner_if(p["project_id"], anon_owner, user_owner):
            claimed += 1
        else:
            skipped += 1
    return {"claimed": claimed, "skipped": skipped}


@router.post("/register", status_code=201)
async def register(body: Credentials, request: Request):
    cfg = request.app.state.config
    if not getattr(cfg, "registration_open", True):
        raise HTTPException(403, {"code": "registration_closed", "message": "注册未开放"})
    store = get_user_store(request)
    try:
        user = store.create_user(body.email, body.password)
    except ValueError as e:
        raise HTTPException(400, {"code": "invalid_registration", "message": str(e)}) from e
    token = store.create_session(user["user_id"], cfg.session_ttl_days)
    claim = _claim(get_repo(request), f"user:{user['user_id']}", body.anon_token)
    return {"session_token": token, "user": user, "claim": claim}


@router.post("/login")
async def login(body: Credentials, request: Request):
    store = get_user_store(request)
    cfg = request.app.state.config
    user = store.get_user_by_email(body.email)
    if user is None or not store.verify_password(user["user_id"], body.password):
        raise HTTPException(401, {"code": "invalid_credentials", "message": "邮箱或密码不正确"})
    token = store.create_session(user["user_id"], cfg.session_ttl_days)
    claim = _claim(get_repo(request), f"user:{user['user_id']}", body.anon_token)
    return {"session_token": token, "user": user, "claim": claim}


@router.post("/logout", status_code=204)
async def logout(request: Request):
    raw = (request.headers.get("X-Owner-Token") or "").strip()
    if raw.startswith("sess_"):
        get_user_store(request).revoke_session(raw)
    return None


@router.get("/me")
async def me(request: Request, owner: str | None = Depends(owner_from_request)):
    if owner and owner.startswith("user:"):
        u = get_user_store(request).get_user(owner.split(":", 1)[1])
        return {"anonymous": False, "user": u}
    return {"anonymous": True}
