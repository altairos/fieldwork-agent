# tests/test_web_app_token.py
"""审计 F02：App Token 访问控制（后端中间件）。"""

import pytest
from fieldwork_agent.web_host.app import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def protected_app(tmp_path, fake_config):
    fake_config.app_token = "secret-token"
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    _app = create_app(config=fake_config, repo=repo)
    init_deps(_app, config=fake_config, repo=repo)
    return _app


@pytest.fixture
async def client(protected_app):
    transport = ASGITransport(app=protected_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_missing_token_rejected(client):
    r = await client.get("/projects")
    assert r.status_code == 401
    assert r.json()["code"] == "app_token_required"


async def test_wrong_token_rejected(client):
    r = await client.get("/projects", headers={"X-App-Token": "wrong"})
    assert r.status_code == 401


async def test_correct_token_accepted(client):
    r = await client.get("/projects", headers={"X-App-Token": "secret-token"})
    assert r.status_code == 200


async def test_settings_protected_too(client):
    r = await client.get("/settings/status")
    assert r.status_code == 401


async def test_health_exempt_and_reports_auth(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "auth_required": True}


async def test_unconfigured_token_means_open(tmp_path, fake_config):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    _app = create_app(config=fake_config, repo=repo)
    init_deps(_app, config=fake_config, repo=repo)
    async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as c:
        r = await c.get("/projects")
        assert r.status_code == 200
        h = await c.get("/health")
        assert h.json()["auth_required"] is False


async def test_cors_preflight_passes_with_token_enabled(client):
    r = await client.options("/projects", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-app-token",
    })
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") is not None


async def test_401_carries_cors_headers(client):
    r = await client.get("/projects", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") is not None
