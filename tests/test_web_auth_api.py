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


async def test_login_invalid_credentials_same_shape(app):
    async with make_client(app, owner_token=None) as c:
        await c.post("/auth/register", json={"email": "a@b.com", "password": "s3cret-pass"})
        r1 = await c.post("/auth/login", json={"email": "a@b.com", "password": "wrong-pass"})
        r2 = await c.post("/auth/login", json={"email": "nobody@b.com", "password": "whatever1"})
        for r in (r1, r2):
            assert r.status_code == 401 and r.json()["detail"]["code"] == "invalid_credentials"


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
    app.state.repo.create("p_legacy", owner="anon:sess_legacy0001")
    async with make_client(app, owner_token=None) as c:
        r = await c.post("/auth/register", json={
            "email": "legacy@b.com", "password": "s3cret-pass",
            "anon_token": "sess_legacy0001"})
        assert r.status_code == 201
        assert r.json()["claim"]["claimed"] == 1
