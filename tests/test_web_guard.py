# tests/test_web_guard.py
"""审计 F01：Web 项目/回合 ID 守卫（目录穿越封堵）。"""

import pytest
from fieldwork_agent.web_host.app import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    _app = create_app(config=None, repo=repo)
    # ASGITransport 不触发 lifespan，手动初始化依赖
    init_deps(_app, config=None, repo=repo)
    return _app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_create_traversal_pid_rejected_no_side_effect(client, tmp_path):
    r = await client.post("/projects", json={"project_id": "../../evil_rel"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_project_id"
    # 审计验收：恶意 ID 无越界副作用
    assert not (tmp_path.parent / "evil_rel").exists()
    assert list(tmp_path.rglob("*evil*")) == []
    lst = await client.get("/projects")
    assert lst.json() == []


async def test_create_other_malformed_pids_rejected(client):
    for bad in ("..\\..\\evil", "sub\\dir", "C:evil"):
        r = await client.post("/projects", json={"project_id": bad})
        assert r.status_code == 400, bad


async def test_get_traversal_pid_rejected(client):
    r = await client.get("/projects/C:evil")
    assert r.status_code == 400


async def test_export_traversal_pid_rejected(client):
    r = await client.get("/projects/C:evil/proposal")
    assert r.status_code == 400


async def test_turn_guard_precedes_auth(client):
    # 守卫 400 应先于 _resolve_clients 的 403 llm_key_required（语义错误先于鉴权报告）
    r = await client.post("/projects/C:evil/messages", json={"text": "hi"})
    assert r.status_code == 400


async def test_turn_id_guard(client):
    await client.post("/projects", json={"project_id": "p1"})
    r = await client.get("/projects/p1/turns/zzz/events")
    assert r.status_code == 400
    r = await client.post("/projects/p1/turns/zzz/cancel")
    assert r.status_code == 400


async def test_double_dot_inside_pid_accepted(client):
    """整值匹配语义：study..2024 合法（与 MCP 侧行为一致）。"""
    r = await client.post("/projects", json={"project_id": "study..2024"})
    assert r.status_code == 201
