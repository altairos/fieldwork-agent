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
