# web_host/api/auth.py
"""账号体系端点：注册/登录/登出/me + 登录认领（CAS，R2）。"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fieldwork_agent.kernel.repository.guard import OWNER_TOKEN_RE
from fieldwork_agent.web_host.api.owner import owner_from_request
from fieldwork_agent.web_host.deps import get_repo, get_user_store
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str
    password: str
    anon_token: str | None = None


def _validated_anon_token(anon_token: str | None) -> str | None:
    """anon_token 形状与 X-Owner-Token 头同规（OWNER_TOKEN_RE 单一来源）。

    非法形状（含误传 ``anon:`` 归一化值）→ 400 拒绝而非静默 0 认领（fail-fast）；
    校验先于建用户/发会话等副作用——否则注册重试会撞邮箱已注册。
    空串视同未携带。
    """
    if anon_token and not OWNER_TOKEN_RE.fullmatch(anon_token):
        raise HTTPException(400, {"code": "invalid_anon_token",
                                  "message": "anon_token 须为裸令牌（勿带 anon: 前缀）"})
    return anon_token


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
    anon_token = _validated_anon_token(body.anon_token)
    store = get_user_store(request)
    try:
        # A03：PBKDF2（600k 迭代）是同步 CPU 密集调用，直接在事件循环里跑会
        # 阻塞并发请求（含 /turns 心跳 SSE）——外移线程池，迭代数不变
        user = await asyncio.to_thread(store.create_user, body.email, body.password)
    except ValueError as e:
        raise HTTPException(400, {"code": "invalid_registration", "message": str(e)}) from e
    token = store.create_session(user["user_id"], cfg.session_ttl_days)
    claim = await asyncio.to_thread(
        _claim, get_repo(request), f"user:{user['user_id']}", anon_token)
    return {"session_token": token, "user": user, "claim": claim}


def _invalid_credentials() -> HTTPException:
    return HTTPException(401, {"code": "invalid_credentials",
                              "message": "邮箱或密码不正确"})


@router.post("/login")
async def login(body: Credentials, request: Request):
    anon_token = _validated_anon_token(body.anon_token)
    store = get_user_store(request)
    cfg = request.app.state.config
    user = await asyncio.to_thread(store.get_user_by_email, body.email)
    if user is None:
        # S03：不存在的账号也要跑一次同强度 PBKDF2——否则“立刻返回”与
        # “跑 60 万次迭代”相差数百毫秒，登录耗时本身就是邮箱枚举器
        await asyncio.to_thread(store.burn_password_check, body.password)
        raise _invalid_credentials()
    if not await asyncio.to_thread(
            store.verify_password, user["user_id"], body.password):
        raise _invalid_credentials()
    token = store.create_session(user["user_id"], cfg.session_ttl_days)
    claim = await asyncio.to_thread(
        _claim, get_repo(request), f"user:{user['user_id']}", anon_token)
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
