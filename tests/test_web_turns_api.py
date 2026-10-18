# tests/test_web_turns_api.py
import asyncio

import pytest
from conftest import OWNER, OWNER_TOKEN
from fieldwork_agent.web_host.app import create_app
from httpx import ASGITransport, AsyncClient


class FakeLLM:
    """单轮无工具回复。"""
    def chat_stream(self, messages, **kw):
        return iter([{"delta": "收到"}, {"content": "收到", "tool_calls": None,
                     "finish_reason": "stop", "usage": {}}])
    def chat(self, messages, **kw):
        return {"content": "收到", "tool_calls": None, "finish_reason": "stop", "usage": {}}


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1", owner=OWNER)
    application = create_app(config=None, repo=repo)
    # ASGITransport 不触发 lifespan，手动初始化依赖
    init_deps(application, config=None, repo=repo)
    return application


@pytest.fixture
async def client(app):
    from conftest import make_client

    app.state.llm = FakeLLM()
    async with make_client(app) as c:
        yield c


async def test_create_turn(client):
    r = await client.post("/projects/p1/messages",
                          json={"text": "你好", "idempotency_key": "k1"})
    assert r.status_code == 201
    assert "turn_id" in r.json() and r.json()["status"] == "running"


async def test_idempotent_creation(client):
    r1 = await client.post("/projects/p1/messages",
                           json={"text": "hi", "idempotency_key": "same"})
    r2 = await client.post("/projects/p1/messages",
                           json={"text": "hi", "idempotency_key": "same"})
    assert r1.json()["turn_id"] == r2.json()["turn_id"]


async def test_sse_stream_events(client):
    r = await client.post("/projects/p1/messages", json={"text": "hello"})
    tid = r.json()["turn_id"]
    await asyncio.sleep(0.5)  # 等后台完成
    async with client.stream("GET", f"/projects/p1/turns/{tid}/events") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk
            if "turn_done" in body:
                break
    assert "message_delta" in body and "turn_done" in body


async def test_no_llm_key_rejected_403(app):
    """无百炼 key（页面未传请求头 + 服务端单例 None）：403 llm_key_required。

    设计变更（首页 key 鉴权）：无 key 不再降级为固定文案，而是直接不进入对话。
    钉死 ``app.state.llm = None``，避免开发者本机配置了 key 时单例非 None。
    """
    app.state.llm = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"X-Owner-Token": OWNER_TOKEN}) as c:
        r = await c.post("/projects/p1/messages", json={"text": "你好"})
        assert r.status_code == 403
        assert "turn_id" not in r.json()  # 未创建回合，未进入对话
        assert r.json()["detail"]["code"] == "llm_key_required"


async def test_create_turn_with_page_key_headers(app, monkeypatch):
    """页面 key（X-DashScope-Key / X-FlyAI-Key）：按回合构建客户端注入，403 门放行。

    - 百炼 key 走 ``_build_llm`` 工厂（monkeypatch 录参）：不依赖真实 SDK/网络；
    - 飞猪 key 走模块级 ``FlyAIClient``（monkeypatch 录参）：构造只存 key，轻量；
    - 回合执行用的正是页面 key 构建的 LLM（FakeLLM 回复「收到」出现在事件流）。
    """
    from fieldwork_agent.web_host.api import turns as turns_mod
    from fieldwork_agent.web_host.deps import get_tm_raw

    app.state.llm = None
    built_llm: list[str] = []
    built_flyai: list[str] = []

    def fake_build_llm(api_key, request):
        built_llm.append(api_key)
        return FakeLLM()

    class FakeFlyAIClient:
        def __init__(self, api_key=""):
            built_flyai.append(api_key)

    monkeypatch.setattr(turns_mod, "_build_llm", fake_build_llm)
    monkeypatch.setattr(turns_mod, "FlyAIClient", FakeFlyAIClient)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"X-Owner-Token": OWNER_TOKEN}) as c:
        r = await c.post("/projects/p1/messages", json={"text": "hi"},
                         headers={"X-DashScope-Key": "sk-page",
                                  "X-FlyAI-Key": "fk-page"})
        assert r.status_code == 201
        tid = r.json()["turn_id"]
        await asyncio.sleep(0.5)  # 等后台回合完成
        events = get_tm_raw(app).replay("p1", tid, after_seq=0)
        deltas = [e.data["text"] for e in events if e.event == "message_delta"]
        assert "".join(deltas) == "收到"  # 用的是页面 key 构建的 FakeLLM
    assert built_llm == ["sk-page"]
    assert built_flyai == ["fk-page"]
    assert app.state.llm is None  # 不动启动单例


async def test_settings_status_reports_configured_flags(app):
    """GET /settings/status 只回布尔状态（不回明文 key）。"""
    from types import SimpleNamespace

    app.state.llm = FakeLLM()  # 钉死，避免开发者本机 key 影响判定
    app.state.config = SimpleNamespace(flyai_api_key="fk")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/settings/status")
        assert r.status_code == 200
        assert r.json() == {"dashscope_configured": True, "flyai_configured": True}

    app.state.llm = None
    app.state.config = SimpleNamespace(flyai_api_key="")
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/settings/status")
        assert r.json() == {"dashscope_configured": False, "flyai_configured": False}


async def test_cancel_turn(client):
    r = await client.post("/projects/p1/messages", json={"text": "x"})
    tid = r.json()["turn_id"]
    cr = await client.post(f"/projects/p1/turns/{tid}/cancel")
    assert cr.status_code == 200
    assert cr.json()["status"] in ("cancelled", "completed")


async def test_unknown_project_404(client):
    r = await client.post("/projects/ghost/messages", json={"text": "hi"})
    assert r.status_code == 404


async def test_unknown_turn_404(client):
    # 审计 F01 接线后 turn_id 先过格式守卫（400），未知回合须用合法格式探测 404
    r = await client.get("/projects/p1/turns/decafbad01234567/events")
    assert r.status_code == 404


async def test_sse_replay_race_still_delivers_terminal_event(app):
    """P1-1：回放期间回合完成的竞态下，流仍须送出 turn_done。

    场景：订阅方已收到首个回放帧（generator 挂起在回放 yield，竞态窗口
    打开），此刻后台回合完成（turn_done 落盘 + finish 广播）。修复前
    generator 恢复后见终态直接 return，turn_done 永不送达；修复后先订阅
    再回放，窗口内事件经队列补送。
    """
    from fieldwork_agent.web_host.api.turns import _event_stream
    from fieldwork_agent.web_host.deps import get_tm_raw

    tm = get_tm_raw(app)
    t = tm.create_turn("p1")
    tm.append_event("p1", t.turn_id, "message_delta", {"text": "a"})

    stream = _event_stream(tm, "p1", t.turn_id, 0, t)
    first = await stream.__anext__()  # 回放帧；generator 挂起在此（窗口打开）
    assert "message_delta" in first

    # 窗口内：后台回合完成（新事件落盘 + 终态广播）
    tm.append_event("p1", t.turn_id, "turn_done", {"status": "completed"})
    tm.finish("p1", t.turn_id, "completed")

    second = await stream.__anext__()  # 必须送出 turn_done，而非直接断流
    assert "turn_done" in second
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()  # 哨兵到后干净关流


# ------------------------------------------------ 消息史恢复（刷新回填）


async def test_list_messages_restores_history(client):
    """GET /projects/{pid}/messages：按时间序回 role/content，供前端刷新后回填对话窗。

    只回 role/content（钉死契约）：ts/metadata 是内核内部细节，不进 API 面。
    """
    await client.post("/projects/p1/messages", json={"text": "你好"})
    await asyncio.sleep(0.5)  # 等后台回合完成（assistant 消息落盘）
    m = await client.get("/projects/p1/messages")
    assert m.status_code == 200
    assert m.json() == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "收到"},
    ]


async def test_list_messages_unknown_project_404(client):
    r = await client.get("/projects/ghost/messages")
    assert r.status_code == 404


# ------------------------------------------------ 套餐端点（X-DashScope-Plan）
# 套餐 Key（sk-sp-）与通用端点不互通（401）；页面/服务端各自可声明套餐类型，
# 白名单映射到专属 OpenAI 兼容端点。


async def test_turn_dashscope_plan_header_selects_endpoint(app, monkeypatch):
    """X-DashScope-Plan: coding → 回合级 LLMClient 用 Coding 套餐白名单端点。"""
    import fieldwork_agent.kernel.llm as llm_mod

    app.state.llm = None
    built: list[dict] = []

    def fake_client(api_key, models, base_url=None):
        built.append({"api_key": api_key, "models": models, "base_url": base_url})
        return FakeLLM()

    # turns._build_llm 延迟 import llm 模块属性，monkeypatch 模块级 LLMClient 即生效
    monkeypatch.setattr(llm_mod, "LLMClient", fake_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"X-Owner-Token": OWNER_TOKEN}) as c:
        r = await c.post("/projects/p1/messages", json={"text": "hi"},
                         headers={"X-DashScope-Key": "sk-sp-x",
                                  "X-DashScope-Plan": "coding"})
        assert r.status_code == 201
    assert built == [{"api_key": "sk-sp-x", "models": app.state.config.llm_models,
                      "base_url": "https://coding.dashscope.aliyuncs.com/v1"}]


async def test_turn_unknown_plan_rejected_to_default(app, monkeypatch):
    """未知 plan（含任意 URL）→ base_url None：白名单拒绝，走 SDK 原生端点。"""
    import fieldwork_agent.kernel.llm as llm_mod

    app.state.llm = None
    built: list[dict] = []

    def fake_client(api_key, models, base_url=None):
        built.append({"base_url": base_url})
        return FakeLLM()

    monkeypatch.setattr(llm_mod, "LLMClient", fake_client)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"X-Owner-Token": OWNER_TOKEN}) as c:
        r = await c.post("/projects/p1/messages", json={"text": "hi"},
                         headers={"X-DashScope-Key": "sk-sp-x",
                                  "X-DashScope-Plan": "https://evil.example.com/v1"})
        assert r.status_code == 201
    assert built == [{"base_url": None}]


def test_init_deps_singleton_uses_config_plan(tmp_path, monkeypatch):
    """init_deps：Config.dashscope_plan（FA_DASHSCOPE_PLAN）→ 启动单例 base_url。"""
    from types import SimpleNamespace

    import fieldwork_agent.kernel.llm as llm_mod
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.app import create_app
    from fieldwork_agent.web_host.deps import init_deps

    created: dict = {}

    def fake_client(api_key, models, base_url=None):
        created.update(api_key=api_key, models=models, base_url=base_url)

    monkeypatch.setattr(llm_mod, "LLMClient", fake_client)
    cfg = SimpleNamespace(
        dashscope_available=True, dashscope_api_key="sk-sp-x",
        llm_models={"interview": "q"}, projects_dir=tmp_path,
        flyai_api_key="", dashscope_plan="token",
    )
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    app = create_app(config=None, repo=repo)
    init_deps(app, config=cfg, repo=repo)
    assert created == {
        "api_key": "sk-sp-x", "models": {"interview": "q"},
        "base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    }
