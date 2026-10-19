import pytest
from conftest import OWNER, OWNER_TOKEN, make_client
from fieldwork_agent.web_host.app import create_app


class _FakeLLM:
    def chat_stream(self, messages, **kw):
        return iter([])
    def chat(self, messages, **kw):
        return {"content": "", "tool_calls": None, "finish_reason": "stop", "usage": {}}


@pytest.fixture
def captured():
    return {"owners": []}


@pytest.fixture
def app(fake_config, monkeypatch, captured):
    import fieldwork_agent.web_host.agent.loop as loop
    from fieldwork_agent.web_host.deps import init_deps

    async def fake_run_turn(repo, pid, tid, text, tm, llm, flyai,
                            *, amap_client=None, owner=None, **kw):
        captured["owners"].append(owner)
        tm.finish(pid, tid, "completed")

    monkeypatch.setattr(loop, "run_turn", fake_run_turn)
    _app = create_app(config=fake_config)
    # ASGITransport 不触发 lifespan，手动初始化依赖（owner_from_request 急切访问
    # state.user_store；dashscope_api_key="" 使 llm 单例为 None，403 需替身挡）
    init_deps(_app, config=fake_config)
    _app.state.llm = _FakeLLM()
    return _app


async def test_create_and_retry_both_thread_owner(app, captured):
    async with make_client(app, owner_token=OWNER_TOKEN) as c:
        await c.post("/projects", json={"project_id": "p1"})
        r = await c.post("/projects/p1/messages", json={"text": "你好"})
        assert r.status_code == 201
        tid = r.json()["turn_id"]
        repo = app.state.repo
        # fake run_turn 不落消息，手补 retry 所需尾（meta 绑定 turn_id 过 N12 守卫）
        repo.append_message("p1", "user", "你好", meta={"turn_id": tid})
        repo.append_message("p1", "assistant", "回复", meta={"turn_id": tid})
        rr = await c.post(f"/projects/p1/turns/{tid}/retry")
        assert rr.status_code == 201
    assert captured["owners"] == [OWNER, OWNER]        # create 与 retry 都把 owner 透传到位
