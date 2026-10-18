# tests/test_web_projects_api.py
import pytest
from fieldwork_agent.web_host.app import create_app


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
    from conftest import make_client

    async with make_client(app) as c:
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
    await client.post("/projects", json={"project_id": "p1"})
    # httpx 会规范化 URL 的 ../ 段，穿越形态用 %5C（反斜杠）编码
    r = await client.get("/projects/p1/cached-searches/..%5C..%5Csecret")
    assert r.status_code == 400
    assert "格式非法" in r.json()["detail"]


async def test_r01_wildcard_and_overlong_cid_400(client):
    await client.post("/projects", json={"project_id": "p1"})
    r1 = await client.get("/projects/p1/cached-searches/*")
    assert r1.status_code == 400
    r2 = await client.get(f"/projects/p1/cached-searches/{'x' * 65}")
    assert r2.status_code == 400


async def test_r01_dotted_cid_400(client):
    await client.post("/projects", json={"project_id": "p1"})
    r = await client.get("/projects/p1/cached-searches/spacer.cid")
    assert r.status_code == 400


async def test_r01_encoded_slash_cid_404(client):
    # %2F 解码后带 / → 路由单段不匹配 → 404（回归保护）
    r = await client.get("/projects/p1/cached-searches/a%2Fb")
    assert r.status_code == 404


async def test_r01_cross_project_cid_404(client, app):
    await client.post("/projects", json={"project_id": "p1"})
    await client.post("/projects", json={"project_id": "p2"})
    repo = app.state.repo
    cid = repo.cache_search("p2", "secret-query", "search_hotel", {"items": []})
    r = await client.get(f"/projects/p1/cached-searches/{cid}")
    assert r.status_code == 404
    assert "secret-query" not in r.text


# ────────────────────────────────────────────────────────────────
# 知识库可见面（audit3 Fix 1b）：agent add_knowledge_entry 落盘材料 → REST 可读
# ────────────────────────────────────────────────────────────────


async def test_list_knowledge_returns_entries_with_content(client, app):
    """200 + 按创建顺序返回条目（含正文）——「存了看不见」症状的服务端兑底。"""
    await client.post("/projects", json={"project_id": "p1"})
    repo = app.state.repo
    k1 = repo.add_knowledge("p1", "印度签证准入", "电子签 30 天…", tags=["签证"])
    k2 = repo.add_knowledge("p1", "节庆日历", "排灯节 11 月…", category="general")
    r = await client.get("/projects/p1/knowledge")
    assert r.status_code == 200
    entries = r.json()
    assert [e["id"] for e in entries] == [k1, k2]
    assert entries[0]["title"] == "印度签证准入"
    assert "电子签" in entries[0]["content"]


async def test_list_knowledge_empty_project(client):
    await client.post("/projects", json={"project_id": "p1"})
    r = await client.get("/projects/p1/knowledge")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_knowledge_unknown_project_404(client):
    r = await client.get("/projects/ghost/knowledge")
    assert r.status_code == 404


async def test_list_knowledge_invalid_pid_400(client):
    # 穿越形态用 %5C（反斜杠）编码，同 R01 先例（httpx 会规范化 ../ 段）
    r = await client.get("/projects/..%5C..%5Csecret/knowledge")
    assert r.status_code == 400


# ────────────────────────────────────────────────────────────────
# PUT /projects/{id}/knowledge/{entry_id}：右栏知识条目编辑（P1）
# ────────────────────────────────────────────────────────────────


async def test_update_knowledge_roundtrip(client, app):
    """200 + 返回更新后的条目；落盘可读（PUT 后 GET 读回新值）。"""
    await client.post("/projects", json={"project_id": "p1"})
    kid = app.state.repo.add_knowledge("p1", "印度签证准入", "电子签 30 天")
    r = await client.put(f"/projects/p1/knowledge/{kid}",
                         json={"title": "签证材料", "content": "**加粗**\n第二行"})
    assert r.status_code == 200
    assert r.json()["title"] == "签证材料"
    assert r.json()["content"] == "**加粗**\n第二行"
    entries = (await client.get("/projects/p1/knowledge")).json()
    assert entries[0]["title"] == "签证材料"
    assert entries[0]["id"] == kid


async def test_update_knowledge_partial_omits_absent_fields(client, app):
    """部分更新：未提供的字段不下行（None = 不改）。"""
    await client.post("/projects", json={"project_id": "p1"})
    kid = app.state.repo.add_knowledge("p1", "提纲", "问题1")
    r = await client.put(f"/projects/p1/knowledge/{kid}", json={"content": "问题2"})
    assert r.status_code == 200
    assert r.json()["title"] == "提纲"


async def test_update_knowledge_unknown_entry_404(client):
    await client.post("/projects", json={"project_id": "p1"})
    r = await client.put("/projects/p1/knowledge/know_ghost", json={"content": "x"})
    assert r.status_code == 404


async def test_update_knowledge_unknown_project_404(client):
    r = await client.put("/projects/ghost/knowledge/know_1", json={"content": "x"})
    assert r.status_code == 404


# ────────────────────────────────────────────────────────────────
# DELETE /projects/{id}：删除即整域清除（项目删除按钮的服务端面）
# ────────────────────────────────────────────────────────────────


async def test_delete_project_removes_from_list(client):
    await client.post("/projects", json={"project_id": "p1"})
    await client.post("/projects", json={"project_id": "p2"})
    r = await client.delete("/projects/p1")
    assert r.status_code == 204
    r2 = await client.get("/projects")
    assert [p["project_id"] for p in r2.json()] == ["p2"]
    assert (await client.get("/projects/p1")).status_code == 404


async def test_delete_project_unknown_404(client):
    r = await client.delete("/projects/ghost")
    assert r.status_code == 404


@pytest.fixture
def iso_app(tmp_path):
    """tm root 与 repo 同根的隔离 app：删除端点会清理 turns 磁盘目录，
    不得写进真实 ~/.fieldwork_agent（同 test_init_deps_singleton 的 config 注入手法）。"""
    from types import SimpleNamespace

    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    _app = create_app(config=None, repo=repo, web_dist=tmp_path / "nonexistent")
    init_deps(_app, config=SimpleNamespace(
        projects_dir=tmp_path / "projects", flyai_api_key=""), repo=repo)
    return _app


@pytest.fixture
async def iso_client(iso_app):
    from conftest import make_client

    async with make_client(iso_app) as c:
        yield c


async def test_delete_project_cancels_running_turn(iso_client, iso_app, tmp_path):
    """删除时该项目 running 回合被取消，内存注册表与 turns 磁盘目录一并清除。"""
    await iso_client.post("/projects", json={"project_id": "p1"})
    tm = iso_app.state.turn_manager
    handle = tm.create_turn("p1")
    r = await iso_client.delete("/projects/p1")
    assert r.status_code == 204
    assert handle.status == "cancelled"
    assert handle.cancel_event.is_set()
    assert not (tmp_path / "projects" / "p1").exists()
    assert tm.get_turn("p1", handle.turn_id) is None


# ────────────────────────────────────────────────────────────────
# 归属隔离（spec §归属令牌）：创建需令牌、列表过滤、详情 404 不泄露
# ────────────────────────────────────────────────────────────────


@pytest.fixture
async def client_anon(app):
    from conftest import make_client

    async with make_client(app, owner_token=None) as c:
        yield c


@pytest.fixture
async def client_other(app):
    from conftest import OTHER_TOKEN, make_client

    async with make_client(app, owner_token=OTHER_TOKEN) as c:
        yield c


async def test_create_without_owner_token_400(client_anon):
    r = await client_anon.post("/projects", json={"project_id": "p9"})
    assert r.status_code == 400
    assert r.json()["detail"] == {"code": "owner_token_required",
                                  "message": "创建项目需携带 X-Owner-Token 头"}


async def test_list_without_owner_token_empty(client, client_anon):
    await client.post("/projects", json={"project_id": "mine"})
    r = await client_anon.get("/projects")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_isolated_between_owners(client, client_other):
    await client.post("/projects", json={"project_id": "mine"})
    r = await client_other.get("/projects")
    assert r.status_code == 200
    assert r.json() == []


async def test_detail_hidden_from_other_owner(client, client_other):
    await client.post("/projects", json={"project_id": "mine"})
    r = await client_other.get("/projects/mine")
    assert r.status_code == 404
    assert r.json()["detail"] == "项目不存在: mine"


async def test_detail_without_token_404(client, client_anon):
    await client.post("/projects", json={"project_id": "mine"})
    r = await client_anon.get("/projects/mine")
    assert r.status_code == 404


async def test_invalid_owner_token_shape_400(client):
    # httpx 头值须 ASCII 可编码，中文违规值发不出去——用等价 ASCII 违规形态
    # （空格与 ! 均不在 ^[A-Za-z0-9_-]{1,128}$ 白名单）覆盖同一语义
    r = await client.post("/projects", json={"project_id": "p9"},
                          headers={"X-Owner-Token": "bad token!"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_owner_token"


async def test_delete_hidden_from_other_owner(client, client_other):
    await client.post("/projects", json={"project_id": "mine"})
    r = await client_other.delete("/projects/mine")
    assert r.status_code == 404
    assert (await client.get("/projects/mine")).status_code == 200


async def test_knowledge_hidden_from_other_owner(client, client_other):
    await client.post("/projects", json={"project_id": "mine"})
    r = await client_other.get("/projects/mine/knowledge")
    assert r.status_code == 404
    assert (await client.get("/projects/mine/knowledge")).status_code == 200
