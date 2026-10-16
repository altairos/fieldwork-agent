# tests/test_web_projects_api.py
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


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_create_project(client):
    r = await client.post("/projects", json={"project_id": "p_test",
                                             "discipline": "人类学", "topic": "宗族"})
    assert r.status_code == 201
    assert r.json()["project_id"] == "p_test"
    assert r.json()["phase"] == "research_positioning"


async def test_create_project_auto_id(client):
    r = await client.post("/projects", json={})
    assert r.status_code == 201
    assert r.json()["project_id"].startswith("field_")


async def test_list_projects(client):
    await client.post("/projects", json={"project_id": "p1"})
    await client.post("/projects", json={"project_id": "p2"})
    r = await client.get("/projects")
    assert r.status_code == 200
    assert {"p1", "p2"} <= {p["project_id"] for p in r.json()}


async def test_get_project_snapshot(client):
    await client.post("/projects", json={"project_id": "p1", "topic": "白族"})
    r = await client.get("/projects/p1")
    assert r.status_code == 200
    snap = r.json()
    assert snap["phase"] == "research_positioning"
    assert snap["research_context"]["topic"] == "白族"
    assert "field_sites" in snap and "informants" in snap


async def test_get_project_404(client):
    r = await client.get("/projects/ghost")
    assert r.status_code == 404


async def test_cached_searches_endpoints(client, app):
    """快照 REST（spec §5.2）：索引列表（不含 result）+ 单条全量 + 404。"""
    await client.post("/projects", json={"project_id": "p1"})
    repo = app.state.repo
    cid = repo.cache_search("p1", 'search_hotels {"dest_name": "大理"}',
                            "search_hotels", {"items": [{"name": "H", "price": 100}]})
    r = await client.get("/projects/p1/cached-searches")
    assert r.status_code == 200
    index = r.json()
    assert [c["id"] for c in index] == [cid]
    assert "result" not in index[0]
    r2 = await client.get(f"/projects/p1/cached-searches/{cid}")
    assert r2.status_code == 200 and r2.json()["result"]["items"][0]["name"] == "H"
    r3 = await client.get("/projects/p1/cached-searches/srch_ghost")
    assert r3.status_code == 404
