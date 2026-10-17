# tests/test_web_mainline_e2e.py
"""主线端到端验收（审计第二批收口）：空项目 → finished 全链 + 持久化 + stale 失效。

覆盖审计第二批退出条件：通过真实 FastAPI 用户入口从空项目走到 finished；
刷新（重建 repo）后状态和文档仍在；修改预算后旧方案确认失效（stale）。
确定性 LLM stub 驱动，不联网（受控真实 API 验收另见部署手册，不入 CI）。
"""
import asyncio

import pytest
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.app import create_app
from httpx import ASGITransport, AsyncClient


class ScriptLLM:
    """回合脚本 LLM：chat_stream 逐回合弹出帧列表（test_web_agent_loop.FakeLLM
    同构）；chat() 恒返空 content——compose_proposal 内部走确定性模板降级
    （Task 4 ChatFakeLLM 模式）。sent 记录每次 chat_stream 收到的 messages，
    供 F09 断言。"""

    def __init__(self, scripts: list[list[dict]]):
        self.scripts = [list(s) for s in scripts]
        self.sent: list[list[dict]] = []

    def chat_stream(self, messages, **kwargs):
        self.sent.append(messages)
        return iter(self.scripts.pop(0))

    def chat(self, messages, **kwargs):
        return {"content": ""}


_PROPOSAL_ARGS = {
    "ctx": {
        "discipline": "社会学", "topic": "宗族变迁", "scale": "单人调研",
        "budget": "2万", "time_window": "7月",
        "departure_city": "上海", "target_location": "大理",
    },
    "sites": [{"name": "大理", "confirmed": True, "profile": {"location": "大理"}}],
    "itinerary": {"legs": []},
    "informants": [{"code": "INF-01", "type": "宗族长老", "relation": "同乡会引荐",
                    "contact": "", "notes": ""}],
}


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    _app = create_app(config=None, repo=repo)
    # ASGITransport 不触发 lifespan，手动初始化依赖
    init_deps(_app, config=None, repo=repo)
    _app.state.projects_dir = tmp_path / "projects"  # ⑧ 重建验证用
    return _app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _post_message(client: AsyncClient, text: str) -> None:
    resp = await client.post("/projects/p1/messages", json={"text": text})
    assert resp.status_code == 201
    await asyncio.sleep(0.5)  # 后台回合（test_web_turns_api.py 同款等待模式）


async def test_mainline_empty_project_to_finished(app, client):
    llm = ScriptLLM([
        # 回合 1 第 1 帧：update_research_context 工具调用（方案 B 深挖：
        # 写入 purpose/scale/research_questions，reason 经 data 通道下发给前端状态卡）
        [{"content": "", "tool_calls": [
            {"id": "c0", "name": "update_research_context",
             "arguments": {"patch": {"purpose": "课程作业", "scale": "单人调研",
                                     "research_questions": "宗族网络如何在城市化中维系？"},
                            "reason": "用户确认研究问题与调研安排"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        # 回合 1 第 2 帧：收尾文本（轮末确定性抽取仍会跑，Task 2/4）
        [{"delta": "好的"}, {"content": "好的，我们开始梳理研究信息。", "usage": {}}],
        # 回合 2 第 1 帧：compose_proposal 工具调用（F04 落盘）
        [{"content": "", "tool_calls": [
            {"id": "c1", "name": "compose_proposal", "arguments": _PROPOSAL_ARGS}],
          "finish_reason": "tool_calls", "usage": {}}],
        # 回合 2 第 2 帧：收尾文本
        [{"delta": "已"}, {"content": "已生成研究方案。", "usage": {}}],
    ])

    # ① 建项：discipline/topic 直接入 ctx（A 阶段七字段已答 2）
    resp = await client.post("/projects", json={"project_id": "p1",
                                                "discipline": "社会学",
                                                "topic": "宗族变迁"})
    assert resp.status_code == 201
    app.state.llm = llm

    # ② 第一回合：工具调用写入 purpose/scale/research_questions（方案 B 深挖），
    # 轮末抽取补齐 budget/time_window/departure_city/target_location（F03）
    # → A→B（7/7 字段全齐 ≥ MIN_POSITIONING_ANSWERED）
    await _post_message(client, "我从上海出发去大理调研，预算2万，2026年7月去")
    snap = (await client.get("/projects/p1")).json()
    ctx = snap["research_context"]
    assert "上海" in str(ctx.get("departure_city", ""))
    assert "大理" in str(ctx.get("target_location", ""))
    assert "2万" in str(ctx.get("budget", ""))
    assert "7月" in str(ctx.get("time_window", ""))
    assert "宗族网络" in str(ctx.get("research_questions", ""))
    assert snap["phase"] == "field_site_profiles"
    # F09：用户消息恰发送一次（历史含本条 + build 不再重复附加）
    user_texts = [str(m.get("content", "")) for m in llm.sent[0]
                  if m.get("role") == "user"]
    assert sum("上海" in t for t in user_texts) == 1

    # ③ 确认田野点：差 informant，不推进
    repo = app.state.repo
    sid = repo.save_field_site("p1", {"name": "大理", "confirmed": False,
                                      "profile": {"location": "大理"}})
    resp = await client.post(f"/projects/p1/field-sites/{sid}/confirm")
    assert resp.status_code == 200
    assert resp.json()["field_site"]["confirmed"] is True
    assert resp.json()["phase_transition"] is None

    # ④ 采纳报道人 → B→C
    resp = await client.post("/projects/p1/informants", json={
        "candidate": {"type": "宗族长老", "approach": "同乡会引荐"}})
    assert resp.status_code == 201
    assert resp.json()["informant"]["code"] == "INF-01"
    assert resp.json()["phase_transition"] == {"from": "field_site_profiles",
                                               "to": "itinerary_planning"}

    # ⑤ 行程 + travel_date/purpose → C→D（has_itinerary 走 cache_search 判定）
    repo.cache_search("p1", "itinerary::大理", "compose_itinerary",
                      {"destinations": ["大理"], "legs": []})
    resp = await client.put("/projects/p1/research-context",
                            json={"travel_date": "2026-07-10", "purpose": "课程作业"})
    assert resp.status_code == 200
    assert resp.json()["research_context"]["travel_date"] == "2026-07-10"
    assert resp.json()["phase_transition"] == {"from": "itinerary_planning",
                                               "to": "proposal_generation"}

    # ⑥ 第二回合：compose_proposal 落盘（F04）+ by-id 可读（F13）
    await _post_message(client, "生成方案")
    docs = (await client.get("/projects/p1/proposals")).json()
    assert len(docs) == 1
    assert docs[0]["kind"] == "proposal"
    latest = (await client.get("/projects/p1/proposal")).json()
    assert latest["markdown"].startswith("# ")
    by_id = (await client.get(f"/projects/p1/proposals/{docs[0]['id']}")).json()
    assert by_id["id"] == docs[0]["id"]
    assert by_id["markdown"] == latest["markdown"]
    snap = (await client.get("/projects/p1")).json()
    assert snap["phase"] == "proposal_generation"  # 草稿未确认不推进（R3 三态）

    # ⑦ 确认方案 → finished（F04）
    resp = await client.post("/projects/p1/proposal/confirm",
                             json={"doc_id": docs[0]["id"]})
    assert resp.status_code == 200
    assert resp.json()["phase_transition"] == {"from": "proposal_generation",
                                               "to": "finished"}
    snap = (await client.get("/projects/p1")).json()
    assert snap["phase"] == "finished"
    assert snap["proposal_confirmed"] is True
    assert snap["proposal_confirmation_stale"] is False

    # ⑧ 持久化：重建 repo（模拟刷新/重启）后状态与文档仍在
    repo2 = JsonRepository(projects_dir=app.state.projects_dir)
    assert repo2.get_phase("p1") == "finished"
    assert len(repo2.list_proposals("p1")) == 1
    assert repo2.list_informants("p1")
    assert repo2.get_state("p1").get("proposal_confirmed") is True

    # ⑨ 修改预算 → 确认失效（stale），阶段不回退
    resp = await client.put("/projects/p1/research-context", json={"budget": "5万"})
    assert resp.status_code == 200
    snap = (await client.get("/projects/p1")).json()
    assert snap["proposal_confirmation_stale"] is True
    assert snap["phase"] == "finished"
