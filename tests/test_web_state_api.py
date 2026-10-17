# tests/test_web_state_api.py
"""状态修正与确认 API（审计第二批 F03/F04）：修正/确认/采纳/方案确认+stale。"""
import pytest
from fieldwork_agent.web_host.app import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    _app = create_app(config=None, repo=repo)
    # ASGITransport 不触发 lifespan，手动初始化依赖
    init_deps(_app, config=None, repo=repo)
    return _app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_update_research_context(client):
    r = await client.put("/projects/p1/research-context",
                         json={"budget": "5万", "scale": "两人同行"})
    assert r.status_code == 200
    ctx = r.json()["research_context"]
    assert ctx["budget"] == "5万" and ctx["scale"] == "两人同行"


async def test_r05_empty_patch_422(client):
    """审计二 R05：空 patch → 422（统一错误语义，不再 400/422 混用）。"""
    r = await client.put("/projects/p1/research-context", json={})
    assert r.status_code == 422
    assert "patch 为空" in r.text


async def test_update_research_context_unknown_404(client):
    r = await client.put("/projects/ghost/research-context", json={"budget": "5万"})
    assert r.status_code == 404


async def test_confirm_field_site(client, app):
    repo = app.state.repo
    sid = repo.save_field_site("p1", {"name": "大理", "confirmed": False,
                                      "profile": {"location": "大理"}})
    r = await client.post(f"/projects/p1/field-sites/{sid}/confirm")
    assert r.status_code == 200
    assert r.json()["field_site"]["confirmed"] is True
    r2 = await client.post("/projects/p1/field-sites/nope/confirm")
    assert r2.status_code == 404


async def test_adopt_informant_seq_codes(client):
    cand = {"type": "宗族长老", "approach": "同乡会引荐", "cautions": "避免直接问族产",
            "question_domains": ["族谱", "祭祀"], "ethics": "匿名处理",
            "access_channel": "村委会介绍", "preparation": "先读村志"}
    r = await client.post("/projects/p1/informants", json={"candidate": cand})
    assert r.status_code == 201
    inf = r.json()["informant"]
    assert inf["code"] == "INF-01" and inf["id"]
    assert "匿名处理" in inf["notes"]
    r2 = await client.post("/projects/p1/informants", json={"candidate": cand})
    assert r2.status_code == 201
    assert r2.json()["informant"]["code"] == "INF-02"


async def test_adopt_informant_type_error_400(client):
    r = await client.post("/projects/p1/informants",
                          json={"candidate": {"question_domains": "不是列表"}})
    assert r.status_code == 400


async def test_confirm_proposal_flow_and_stale(client, app):
    repo = app.state.repo
    did = repo.save_proposal("p1", "# 研究方案\n\n## 一、背景\n\n内容")
    r = await client.post("/projects/p1/proposal/confirm", json={"doc_id": did})
    assert r.status_code == 200
    assert r.json()["confirmation"]["subject"] == did
    assert r.json()["proposal_confirmed"] is True
    snap = (await client.get("/projects/p1")).json()
    assert snap["proposal_confirmed"] is True
    assert snap["proposal_confirmation_stale"] is False
    # 修改预算 → 确认失效（审计二 R09：五路依赖指纹不一致）
    await client.put("/projects/p1/research-context", json={"budget": "9万"})
    snap2 = (await client.get("/projects/p1")).json()
    assert snap2["proposal_confirmation_stale"] is True


async def test_r05_type_violation_422(client):
    """审计二 R05：strict 模式拒绝字符串字段收数字（此前 5 被静默写入）。"""
    r = await client.put("/projects/p1/research-context", json={"budget": 5})
    assert r.status_code == 422


async def test_r05_extra_field_422(client):
    """审计二 R05：extra=forbid 拒绝白名单外键（此前被静默忽略）。"""
    r = await client.put("/projects/p1/research-context",
                         json={"budget": "5万", "hacked": "x"})
    assert r.status_code == 422


async def test_r05_na_without_reason_422(client):
    """审计二 R05：NA=True 无理由 → 422 且 detail 指明字段。"""
    r = await client.put("/projects/p1/research-context",
                         json={"interviews_not_applicable": True})
    assert r.status_code == 422
    assert "interviews_na_reason" in r.text


async def test_r09_confirm_requires_doc_id_422(client):
    """审计二 R09：REST 强制 doc_id——空 body 折 422（不再隐式确认『最新一份』）。"""
    r = await client.post("/projects/p1/proposal/confirm", json={})
    assert r.status_code == 422


async def test_r09_confirm_not_found_404(client, app):
    """审计二 R09：确认不存在的文档 → 404（此前指向不明的『最新』语义）。"""
    r = await client.post("/projects/p1/proposal/confirm",
                          json={"doc_id": "prop_20990101_deadbeef"})
    assert r.status_code == 404
    assert "文档不存在" in r.text


async def test_r09_confirm_wrong_kind_400(client, app):
    """审计二 R09：确认访谈提纲 → 400（此前 kind 不校验，任何文档都能确认）。"""
    repo = app.state.repo
    gid = repo.save_proposal("p1", "# 提纲", kind="interview_guide")
    r = await client.post("/projects/p1/proposal/confirm", json={"doc_id": gid})
    assert r.status_code == 400
    assert "interview_guide" in r.text


async def test_r09_confirm_dep_hash_mismatch_409(client, app):
    """审计二 R09：依赖变化后确认同一文档 → 409（防旧输入上的确认）。"""
    repo = app.state.repo
    did = repo.save_proposal("p1", "# 研究方案\n\n## 一、背景\n\n内容")
    await client.put("/projects/p1/research-context", json={"budget": "9万"})
    r = await client.post("/projects/p1/proposal/confirm", json={"doc_id": did})
    assert r.status_code == 409
    assert "重新生成" in r.text
