"""L3 契约测试（spec §4 同构三件套 + 资源面）：真实 MCP 会话级断言，不 mock 协议层。

用内存流跑真 ``ClientSession`` ↔ 低层 ``Server``：SDK 2.3.0 没有 1.x 的
``create_connected_server_and_client_session``，这里以 ``create_client_server_memory_streams``
+ anyio task group 起 ``srv.run`` 自建会话夹具 ``_session``（形状对照 SDK
``mcp/client/_memory.py`` 的 InMemoryTransport）。
"""
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import pytest
from fieldwork_agent.kernel import build_tool_schemas
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.mcp_server.server import create_server
from mcp import ClientSession
from mcp.server.lowlevel import Server as LowLevelServer
from mcp.shared.memory import create_client_server_memory_streams

EXPECTED = 22


@pytest.fixture
def server(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    return create_server(config=None, repo=repo)   # config=None → 无 LLM、搜索走真实替身


@asynccontextmanager
async def _session(server: LowLevelServer) -> AsyncIterator[ClientSession]:
    """内存 client↔server 会话夹具：task group 起 server 侧，退出时取消收尾。"""
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        server_read, server_write = server_streams
        client_read, client_write = client_streams
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.run, server_read, server_write,
                          server.create_initialization_options())
            try:
                async with ClientSession(client_read, client_write) as session:
                    await session.initialize()
                    yield session
            finally:
                tg.cancel_scope.cancel()


async def test_schema_isomorphism(server):
    """L3①：MCP 注册的工具名+inputSchema 与 kernel build_tool_schemas() 逐一对齐。"""
    async with _session(server) as client:
        tools = (await client.list_tools()).tools
    assert len(tools) == EXPECTED
    kernel_side = {t["function"]["name"]: t["function"]["parameters"]
                   for t in build_tool_schemas()}
    for t in tools:
        if t.name in kernel_side:
            assert t.input_schema == kernel_side[t.name], f"{t.name} schema 漂移"
    assert {t.name for t in tools} - set(kernel_side) == {
        "create_project", "list_projects", "get_project", "update_research_context",
        "save_field_site", "save_informant", "add_knowledge_entry",
        "search_knowledge", "check_phase_advance"}


async def test_state_tool_roundtrip(server):
    async with _session(server) as client:
        res = await client.call_tool("create_project", {"project_id": "p1"})
        payload = json.loads(res.content[0].text)
        assert payload["ok"] and payload["data"]["project_id"] == "p1"


async def test_search_degrades_without_cli(server, monkeypatch):
    # 钉死"测试环境无 flyai CLI"前提（本机可能装有 npm 版 flyai）：在 exec 边界模拟
    # FileNotFoundError，让 FlyAIClient._run_command 走真实的降级映射链路
    from fieldwork_agent.kernel.sources import flyai as flyai_mod

    def _no_cli(*args, **kwargs):
        raise FileNotFoundError("flyai CLI not found")

    monkeypatch.setattr(flyai_mod.subprocess, "run", _no_cli)
    async with _session(server) as client:
        res = await client.call_tool("search_hotels", {"dest_name": "大理"})
        payload = json.loads(res.content[0].text)
    # 测试环境无 flyai CLI：必须走统一包络降级而非协议异常
    assert payload["ok"] is False
    assert payload["error"]["code"] in ("flyai_unavailable", "source_exception")


async def test_resources_and_prompts_visible(server):
    async with _session(server) as client:
        assert (await client.list_resources()).resources
        got = await client.read_resource("fieldwork://guidance/phases")
        assert "proposal_confirmation" in got.contents[0].text
        assert len((await client.list_prompts()).prompts) == 2


async def test_compose_proposal_degraded_visible_without_key(server):
    """终审 I2（验收③）：config=None（无 LLM）时 compose_proposal 包络必须机器可见 degraded。

    最小合法参数覆盖 build_context_block 四硬渠道（研究者/田野点/行程/访谈对象，
    形状对照 tests/test_documents.py 的 CTX/SITES 与 test_registry_surface 的 _FULL_ARGS）；
    server 夹具 config=None → host_ctx["llm"]=None → proposal_handler 模板降级分支
    带 degraded 键（本次 kernel 唯一豁免改动），envelope_dict(exclude_none) 后仍可见。
    """
    args = {
        "ctx": {"discipline": "社会学", "topic": "宗族变迁", "target_location": "喜洲"},
        "sites": [{"name": "喜洲", "confirmed": True}],
        "itinerary": {"legs": [{"from": "昆明", "to": "大理"}]},
        "informants": [{"code": "P1", "type": "村干部"}],
        "knowledge": [],
    }
    async with _session(server) as client:
        res = await client.call_tool("compose_proposal", args)
        payload = json.loads(res.content[0].text)
    assert payload["ok"] is True, payload.get("error")
    assert payload.get("degraded"), "无 key 时 degraded 必须机器可见（验收③）"
