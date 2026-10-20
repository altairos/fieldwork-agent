# tests/test_web_auth_api.py
import pytest
from conftest import OWNER_TOKEN, make_client
from fieldwork_agent.web_host.app import create_app


@pytest.fixture
def app(fake_config):
    from fieldwork_agent.web_host.deps import init_deps

    _app = create_app(config=fake_config)
    # ASGITransport 不触发 lifespan，手动初始化依赖
    init_deps(_app, config=fake_config)
    return _app


async def test_register_login_me_logout(app):
    async with make_client(app, owner_token=None) as c:
        r = await c.post("/auth/register",
                         json={"email": "a@b.com", "password": "s3cret-pass"})
        assert r.status_code == 201
        tok = r.json()["session_token"]
        assert tok.startswith("sess_")
        me = await c.get("/auth/me", headers={"X-Owner-Token": tok})
        assert me.json()["anonymous"] is False
        assert me.json()["user"]["email"] == "a@b.com"
        lo = await c.post("/auth/logout", headers={"X-Owner-Token": tok})
        assert lo.status_code == 204
        me2 = await c.get("/auth/me", headers={"X-Owner-Token": tok})
        assert me2.status_code == 401 and me2.json()["detail"]["code"] == "session_expired"


async def test_register_rejects_short_anon_token(app):
    """S04：anon_token 是批量认领他人项目的凭据，短令牌（可穷举）一律 400。"""
    async with make_client(app, owner_token=None) as c:
        r = await c.post("/auth/register", json={
            "email": "s4@b.com", "password": "s3cret-pass", "anon_token": "short"})
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_anon_token"


async def test_login_invalid_credentials_same_shape(app):
    async with make_client(app, owner_token=None) as c:
        await c.post("/auth/register", json={"email": "a@b.com", "password": "s3cret-pass"})
        r1 = await c.post("/auth/login", json={"email": "a@b.com", "password": "wrong-pass"})
        r2 = await c.post("/auth/login", json={"email": "nobody@b.com", "password": "whatever1"})
        for r in (r1, r2):
            assert r.status_code == 401 and r.json()["detail"]["code"] == "invalid_credentials"


async def test_login_burns_hash_when_account_missing(app):
    """S03：账号不存在也要跑同强度哈希，响应时间不能泄露账号是否存在。"""
    calls: list[str] = []
    app.state.user_store.burn_password_check = calls.append
    async with make_client(app, owner_token=None) as c:
        r = await c.post("/auth/login", json={"email": "nobody@b.com",
                                              "password": "whatever-pass"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "invalid_credentials"
    assert calls == ["whatever-pass"]


async def test_login_does_not_burn_when_account_exists(app):
    """存在账号只走一次真实校验；多烧一次哈希会白白双倍耗时。"""
    calls: list[str] = []
    app.state.user_store.burn_password_check = calls.append
    async with make_client(app, owner_token=None) as c:
        await c.post("/auth/register",
                     json={"email": "a@b.com", "password": "s3cret-pass"})
        r = await c.post("/auth/login", json={"email": "a@b.com", "password": "wrong-pass"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "invalid_credentials"
    assert calls == []


async def test_login_claims_anon_projects(app):
    async with make_client(app, owner_token=OWNER_TOKEN) as anon:
        assert (await anon.post("/projects", json={"project_id": "p_anon"})).status_code in (200, 201)
    async with make_client(app, owner_token=None) as c:
        r = await c.post("/auth/register",
                         json={"email": "a@b.com", "password": "s3cret-pass",
                               "anon_token": OWNER_TOKEN})
        assert r.json()["claim"]["claimed"] == 1
        tok = r.json()["session_token"]
        lst = await c.get("/projects", headers={"X-Owner-Token": tok})
        assert [p["project_id"] for p in lst.json()] == ["p_anon"]


async def test_short_password_400(app):
    async with make_client(app, owner_token=None) as c:
        r = await c.post("/auth/register", json={"email": "a@b.com", "password": "x"})
        assert r.status_code == 400


async def test_register_reject_invalid_anon_token(app):
    # 非法形状（含误传 anon: 归一化值）→ 400，且先于建用户副作用：同邮箱仍可 201
    async with make_client(app, owner_token=None) as c:
        for bad in (f"anon:{OWNER_TOKEN}", "bad token"):
            r = await c.post("/auth/register",
                             json={"email": "a@b.com", "password": "s3cret-pass",
                                   "anon_token": bad})
            assert r.status_code == 400
            assert r.json()["detail"]["code"] == "invalid_anon_token"
        r = await c.post("/auth/register",
                         json={"email": "a@b.com", "password": "s3cret-pass"})
        assert r.status_code == 201

# ---------------------------------------------------------------- A01：REST/MCP 同解
_JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _rpc(method: str, params: dict | None = None, id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _envelope(resp) -> dict:
    import json as _json
    return _json.loads(resp.json()["result"]["content"][0]["text"])


async def test_rest_and_mcp_share_session_owner_and_logout(app):
    """A01：sess_ 令牌在 REST 与 MCP 同解为 user:<id>；注销后两端同时拒绝。"""
    pytest.importorskip("mcp", reason="HTTP 入口依赖 mcp SDK")
    async with make_client(app, owner_token=None) as c:
        reg = await c.post("/auth/register",
                           json={"email": "m@b.com", "password": "s3cret-pass"})
        assert reg.status_code == 201
        tok = reg.json()["session_token"]
        # REST 建项
        r = await c.post("/projects", json={"project_id": "p_shared"},
                         headers={"X-Owner-Token": tok})
        assert r.status_code in (200, 201)
        async with app.state.mcp_service.run():   # manager.run() 只能进一次，覆盖整段
            # MCP 读同一项目（修复前令牌被归一为 anon:sess_*，项目不可见）
            read = await c.post("/mcp", json=_rpc("tools/call", {
                "name": "get_project", "arguments": {"project_id": "p_shared"}}),
                headers={**_JSON_HEADERS, "X-Owner-Token": tok})
            assert read.status_code == 200
            assert _envelope(read)["ok"] is True
            assert _envelope(read)["data"]["project_id"] == "p_shared"
            # 反向：MCP 建项 → REST 读（同一 user 归属）
            created = await c.post("/mcp", json=_rpc("tools/call", {
                "name": "create_project", "arguments": {"project_id": "p_mcp_created"}}),
                headers={**_JSON_HEADERS, "X-Owner-Token": tok})
            assert _envelope(created)["ok"] is True
            lst = await c.get("/projects", headers={"X-Owner-Token": tok})
            assert "p_mcp_created" in [p["project_id"] for p in lst.json()]
            # 注销：REST 401 + MCP 401（同拒）
            assert (await c.post("/auth/logout",
                                 headers={"X-Owner-Token": tok})).status_code == 204
            rest_me = await c.get("/auth/me", headers={"X-Owner-Token": tok})
            assert rest_me.status_code == 401
            mcp_denied = await c.post("/mcp", json=_rpc("tools/call", {
                "name": "get_project", "arguments": {"project_id": "p_shared"}}),
                headers={**_JSON_HEADERS, "X-Owner-Token": tok})
            assert mcp_denied.status_code == 401
            assert mcp_denied.json()["detail"]["code"] == "session_expired"


async def test_register_claims_legacy_anon_sess_projects(app):
    """A01：旧版 MCP 曾把 sess_ 令牌归一为 anon:sess_*；注册携原始令牌可 CAS 认领。"""
    app.state.repo.create("p_legacy", owner="anon:sess_legacy_account_0001")
    async with make_client(app, owner_token=None) as c:
        r = await c.post("/auth/register", json={
            "email": "legacy@b.com", "password": "s3cret-pass",
            "anon_token": "sess_legacy_account_0001"})
        assert r.status_code == 201
        assert r.json()["claim"]["claimed"] == 1


# ---------------------------------------------------------------- A03：PBKDF2 外移线程池
async def test_login_pbkdf2_does_not_block_event_loop(app):
    """A03：登录（PBKDF2 600k）期间事件循环心跳不被阻塞（阈值 150ms）。"""
    import asyncio

    async with make_client(app, owner_token=None) as c:
        await c.post("/auth/register",
                     json={"email": "hb@b.com", "password": "s3cret-pass"})
        gaps: list[float] = []
        stop = asyncio.Event()

        async def ticker():
            loop = asyncio.get_running_loop()
            last = loop.time()
            while not stop.is_set():
                await asyncio.sleep(0.005)
                now = loop.time()
                gaps.append(now - last)
                last = now

        task = asyncio.create_task(ticker())
        await asyncio.sleep(0.02)          # ticker 先进入稳定节拍
        r = await c.post("/auth/login",
                         json={"email": "hb@b.com", "password": "s3cret-pass"})
        stop.set()
        await task
    assert r.status_code == 200
    # 同步 PBKDF2 单次实测 0.2-0.6s（审计实测心跳 188/546ms）；to_thread 后与 5ms 基准同量级
    assert max(gaps) < 0.15, f"事件循环被阻塞 {max(gaps):.3f}s"
