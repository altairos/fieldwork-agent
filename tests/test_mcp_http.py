"""streamable HTTP 传输入口契约测试（Managed Agents 接入改造 1-3，docs/research）。

百炼 Managed Agents 以「远程自定义 MCP（streamableHttp + 动态 Header 代传）」
调用本服务；这里用 httpx ASGITransport 直接打 JSON-RPC 线上形状（不走
ClientSession），覆盖：

- 改造1：stateless + json_response 全链路（initialize / tools/list 23 项 /
  tools/call 状态工具往返），无需会话粘性；
- 改造3：动态 Header ``X-Flyai-Key`` / ``X-Dashscope-Key``（N4 增 ``X-Amap-Key``）
  逐请求注入请求级宿主对象（contextvar → host_ctx 重建），无 Header 时回落组装期 env 兜底；
- 改造2：web_host ``/mcp`` 挂载并入 X-App-Token 守卫（401/200）。
"""
import json
import sys
from contextlib import asynccontextmanager

import httpx
import pytest

pytest.importorskip("mcp", reason="HTTP 入口依赖 mcp SDK（pip install '.[mcp]'）")

from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.mcp_server import dispatch

EXPECTED_TOOLS = 30   # 21 能力（含 amap 六原语 + plan_last_mile）+ 9 状态（与 test_mcp_server_contract.EXPECTED 同源对齐）

HEADERS = {
    "Content-Type": "application/json",
    # json_response 模式下 SDK 只要求 Accept 含 application/json；
    # 同时带 SSE 类型是对真实 MCP 客户端形状的还原
    "Accept": "application/json, text/event-stream",
}


def _rpc(method: str, params: dict | None = None, id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _result(resp: httpx.Response) -> dict:
    resp.raise_for_status()
    body = resp.json()
    assert body.get("error") is None, body
    return body["result"]


def _envelope(resp: httpx.Response) -> dict:
    """tools/call 响应 → 统一包络 dict（content[0].text 是包络 JSON 文本）。"""
    text = _result(resp)["content"][0]["text"]
    return json.loads(text)


# ---------------------------------------------------------------- 夹具


@pytest.fixture
def repo(tmp_path):
    return JsonRepository(projects_dir=tmp_path / "projects")


@pytest.fixture
def service(fake_config, repo):
    """只构建不进 run()：manager 的取消域要求 enter/exit 同一 task，而
    pytest-asyncio 的 async fixture teardown 跑在另一 task（实测 RuntimeError:
    Attempted to exit cancel scope in a different task）→ 由 _client 在测试
    协程内托管。"""
    from fieldwork_agent.mcp_server.http_entry import StreamableHttpService

    return StreamableHttpService(config=fake_config, repo=repo)


@asynccontextmanager
async def _client(service):
    """测试协程内进出 service.run()（同 task，取消域对称），ASGITransport
    不跑 lifespan 所以必须手动托管 manager lifespan。"""
    async with service.run():
        transport = httpx.ASGITransport(app=service.asgi)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


# ---------------------------------------------------------------- 改造1：stateless 全链路


async def test_stateless_http_roundtrip(service):
    """initialize → tools/list（23 项）→ tools/call（create_project 往返）。"""
    async with _client(service) as client:
        init = _result(await client.post("/", json=_rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "http-contract", "version": "0"},
        }), headers=HEADERS))
        assert init["serverInfo"]["name"] == "fieldwork-agent"

        # stateless：无会话粘性，第二个请求（新 transport）直接 tools/list
        listed = _result(await client.post("/", json=_rpc("tools/list"), headers=HEADERS))
        names = {t["name"] for t in listed["tools"]}
        assert len(names) == EXPECTED_TOOLS

        called = _envelope(await client.post("/", json=_rpc("tools/call", {
            "name": "create_project", "arguments": {"project_id": "p1"},
        }), headers=HEADERS))
        assert called["ok"], called.get("error")
        assert called["data"]["project_id"] == "p1"


# ---------------------------------------------------------------- 改造3：动态 Header → 请求级宿主对象


async def test_flyai_key_header_is_per_request(service, fake_config, monkeypatch):
    """X-Flyai-Key 逐请求生效：key-a / key-b / 无 Header（回落组装期 env 值）。"""
    captured: dict = {}

    def fake_search(*, client, **kwargs):
        captured["client"] = client
        return {"stub": True}

    # 单例表钉空，强制走 _CAPABILITY_FNS 注入路径（改造3 的 dispatch 感知语义）
    monkeypatch.setitem(dispatch._CLIENTLESS_SINGLETONS, "search_hotels", None)
    monkeypatch.setitem(dispatch._CAPABILITY_FNS, "search_hotels", fake_search)

    async with _client(service) as client:
        async def _call(flyai_key: str | None):
            headers = dict(HEADERS)
            if flyai_key:
                headers["X-Flyai-Key"] = flyai_key
            resp = await client.post("/", json=_rpc("tools/call", {
                "name": "search_hotels", "arguments": {"dest_name": "大理"},
            }), headers=headers)
            assert _envelope(resp)["ok"] is True
            return captured.pop("client")

        assert (await _call("key-a")).api_key == "key-a"
        assert (await _call("key-b")).api_key == "key-b"      # 请求间互不串
        assert (await _call(None)).api_key == fake_config.flyai_api_key   # env 兜底（空）


async def test_dashscope_key_header_is_per_request(service, fake_config, monkeypatch):
    """X-Dashscope-Key 逐请求重建 LLMClient；无 Header 保组装期 llm（fake 下为 None）。"""
    import fieldwork_agent.kernel.llm as llm_mod

    constructed: list = []

    class SpyLLM:
        def __init__(self, api_key, models, base_url=None):
            constructed.append({"api_key": api_key, "models": models})
            self.api_key = api_key

    captured: dict = {}

    def fake_compose(*, llm, **kwargs):
        captured["llm"] = llm
        return {"stub": True}

    monkeypatch.setattr(llm_mod, "LLMClient", SpyLLM)
    monkeypatch.setitem(dispatch._CAPABILITY_FNS, "compose_proposal", fake_compose)

    async with _client(service) as client:
        async def _call(dashscope_key: str | None):
            constructed.clear()
            headers = dict(HEADERS)
            if dashscope_key:
                headers["X-Dashscope-Key"] = dashscope_key
            resp = await client.post("/", json=_rpc("tools/call", {
                "name": "compose_proposal",
                "arguments": {"ctx": {"topic": "宗族变迁"}, "sites": [], "itinerary": {},
                              "informants": [], "knowledge": []},
            }), headers=headers)
            assert _envelope(resp)["ok"] is True
            return captured.pop("llm"), list(constructed)

        llm_a, cons_a = await _call("ds-key-a")
        assert llm_a.api_key == "ds-key-a"
        assert cons_a[0]["models"] == fake_config.llm_models   # 模型三槽沿用组装期配置

        llm_b, _ = await _call("ds-key-b")
        assert llm_b.api_key == "ds-key-b"

        llm_none, cons_none = await _call(None)
        assert llm_none is None              # 组装期 host_ctx["llm"]（fake_config 无 key）
        assert cons_none == []               # 无 Header 不构造新 LLMClient


async def test_amap_key_header_is_per_request(service, fake_config, monkeypatch):
    """X-Amap-Key 逐请求生效（PoC2 §4.2）：key-a / key-b / 无 Header 回落组装期 env 值。"""
    captured: dict = {}

    def fake_geocode(*, amap_client, **kwargs):
        captured["client"] = amap_client
        return {"stub": True}

    # 单例表钉空，强制走 _CAPABILITY_FNS 注入路径（与 X-Flyai-Key 用例同构）
    monkeypatch.setitem(dispatch._CLIENTLESS_SINGLETONS, "amap_geocode", None)
    monkeypatch.setitem(dispatch._CAPABILITY_FNS, "amap_geocode", fake_geocode)

    async with _client(service) as client:
        async def _call(amap_key: str | None):
            headers = dict(HEADERS)
            if amap_key:
                headers["X-Amap-Key"] = amap_key
            resp = await client.post("/", json=_rpc("tools/call", {
                "name": "amap_geocode", "arguments": {"address": "喜洲镇"},
            }), headers=headers)
            assert _envelope(resp)["ok"] is True
            return captured.pop("client")

        assert (await _call("key-a")).api_key == "key-a"
        assert (await _call("key-b")).api_key == "key-b"      # 请求间互不串
        assert (await _call(None)).api_key == fake_config.amap_api_key   # env 兜底


async def test_amap_key_header_bypasses_amap_singleton(service, monkeypatch):
    """单例绕过语义平移：带 X-Amap-Key 绕过绑死组装期实例的 amap partial；
    无 Header 仍走 partial（真 fn 无 Key 短路 amap_invalid_key，不出网）。"""
    calls: list = []

    def fake_geocode(*, amap_client, **kwargs):
        calls.append(amap_client)
        return {"stub": True}

    # 不钉空单例表：_CLIENTLESS_SINGLETONS["amap_geocode"] 保持组装期 partial
    monkeypatch.setitem(dispatch._CAPABILITY_FNS, "amap_geocode", fake_geocode)

    async with _client(service) as client:
        no_header = _envelope(await client.post("/", json=_rpc("tools/call", {
            "name": "amap_geocode", "arguments": {"address": "喜洲镇"},
        }), headers=HEADERS))
        assert no_header["ok"] is False
        assert no_header["error"]["code"] == "amap_invalid_key"   # partial 真 fn 短路
        assert calls == []                                        # fake 未被触碰

        with_header = _envelope(await client.post("/", json=_rpc("tools/call", {
            "name": "amap_geocode", "arguments": {"address": "喜洲镇"},
        }), headers={**HEADERS, "X-Amap-Key": "key-x"}))
        assert with_header["ok"] is True
        assert calls and calls[0].api_key == "key-x"              # 绕过 partial 注入新实例


# ---------------------------------------------------------------- 改造2：web_host /mcp 挂载 + 守卫


def test_web_mcp_mount_guarded_by_app_token(tmp_path, fake_config):
    """POST /mcp 并入 X-App-Token 守卫：无/错 token 401，带 token 走通 tools/list。"""
    from fastapi.testclient import TestClient
    from fieldwork_agent.web_host.app import create_app

    fake_config.app_token = "secret-token"
    app = create_app(config=fake_config,
                     repo=JsonRepository(projects_dir=tmp_path / "projects"))
    with TestClient(app) as tc:   # context manager 跑 lifespan：init_deps + service.run()
        r = tc.post("/mcp", json=_rpc("tools/list"), headers=HEADERS)
        assert r.status_code == 401
        assert r.json()["code"] == "app_token_required"

        ok = tc.post("/mcp", json=_rpc("tools/list"),
                     headers={**HEADERS, "X-App-Token": "secret-token"})
        assert ok.status_code == 200
        assert len({t["name"] for t in ok.json()["result"]["tools"]}) == EXPECTED_TOOLS


def test_web_mcp_degrades_without_sdk(tmp_path, fake_config, monkeypatch):
    """改造2 降级承诺：http_entry 不可 import（未装 mcp extra）时不挂 /mcp，宿主不炸。"""
    from fastapi.testclient import TestClient
    from fieldwork_agent.web_host.app import create_app

    # sys.modules 塞 None → "from ... import" 直接 ImportError（模拟 SDK 缺失）
    monkeypatch.setitem(sys.modules, "fieldwork_agent.mcp_server.http_entry", None)
    app = create_app(config=fake_config,
                     repo=JsonRepository(projects_dir=tmp_path / "projects"))
    assert not any(getattr(r, "path", None) == "/mcp" for r in app.routes)
    assert getattr(app.state, "mcp_service", None) is None
    with TestClient(app) as tc:
        assert tc.get("/health").json()["status"] == "ok"
