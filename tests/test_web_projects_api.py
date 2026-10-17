# tests/test_web_projects_api.py
import pytest
from fieldwork_agent.web_host.app import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    # web_dist 指向不存在路径：本文件测 API 分层语义，须与本地 web/dist 构建产物
    # （SPA catch-all 会把路由不匹配的请求应答为 200 index.html）隔离——与
    # test_web_static_mount.py 的 no_dist 先例一致，否则 404 断言随构建状态漂移。
    _app = create_app(config=None, repo=repo, web_dist=tmp_path / "nonexistent")
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


# ────────────────────────────────────────────────────────────────
# 审计二 R01：单条快照端点分层（非法 cid 400 / 不存在与跨项目 404）
# ────────────────────────────────────────────────────────────────


async def test_r01_traversal_cid_rejected_400(client):
    # httpx 会规范化 URL 的 ../ 段，穿越形态用 %5C（反斜杠）编码
    r = await client.get("/projects/p1/cached-searches/..%5C..%5Csecret")
    assert r.status_code == 400
    assert "格式非法" in r.json()["detail"]


async def test_r01_wildcard_and_overlong_cid_400(client):
    r1 = await client.get("/projects/p1/cached-searches/*")
    assert r1.status_code == 400
    r2 = await client.get(f"/projects/p1/cached-searches/{'x' * 65}")
    assert r2.status_code == 400


async def test_r01_dotted_cid_400(client):
    r = await client.get("/projects/p1/cached-searches/spacer.cid")
    assert r.status_code == 400


async def test_r01_encoded_slash_cid_404(client):
    # %2F 解码后带 / → 路由单段不匹配 → 404（回归保护）
    r = await client.get("/projects/p1/cached-searches/a%2Fb")
    assert r.status_code == 404


async def test_r01_cross_project_cid_404(client, app):
    await client.post("/projects", json={"project_id": "p2"})
    repo = app.state.repo
    cid = repo.cache_search("p2", "secret-query", "search_hotel", {"items": []})
    r = await client.get(f"/projects/p1/cached-searches/{cid}")
    assert r.status_code == 404
    assert "secret-query" not in r.text
