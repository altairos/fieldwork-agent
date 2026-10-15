# tests/test_web_turns_api.py
import asyncio

import pytest
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
    repo.create("p1")
    application = create_app(config=None, repo=repo)
    # ASGITransport 不触发 lifespan，手动初始化依赖
    init_deps(application, config=None, repo=repo)
    return application


@pytest.fixture
async def client(app):
    app.state.llm = FakeLLM()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
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
    r = await client.get("/projects/p1/turns/nonexist/events")
    assert r.status_code == 404
