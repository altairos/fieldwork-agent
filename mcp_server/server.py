"""MCP Server 组装（spec §2 薄壳）：零业务逻辑，只做 list/read/call 三路路由。

SDK 2.3.0 低层接线（实测形状，1.x 的 ``@srv.list_tools()`` 装饰器范式已不存在）：
``Server.add_request_handler(method, params_type, handler)``，handler 统一收
``(ctx: ServerRequestContext, params: <params_type 实例>)``、返回 Result 模型——
形状逐字对照 SDK ``mcp/server/mcpserver/server.py`` 里 ``MCPServer._handle_*``
给 ``_lowlevel_server`` 的接线。协议层只认 Result 模型；业务失败一律折进
统一包络 JSON 文本（call_tool）或 MCPError（read/get 路由），不泄裸异常。
"""
import functools
import inspect
import json
from typing import TYPE_CHECKING, Any

import anyio
from fieldwork_agent.kernel.registry import capabilities
from fieldwork_agent.kernel.sources.flyai import FlyAIClient
from fieldwork_agent.mcp_server import dispatch, state_tools
from fieldwork_agent.mcp_server import prompts as prompts_mod
from fieldwork_agent.mcp_server import resources as res_mod
from fieldwork_agent.mcp_server.mapping import capability_tools
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server as LowLevelServer
from mcp.shared.exceptions import MCPError
from mcp.types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    GetPromptRequestParams,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ListToolsResult,
    PaginatedRequestParams,
    PromptMessage,
    ReadResourceRequestParams,
    ReadResourceResult,
    TextContent,
    TextResourceContents,
)

if TYPE_CHECKING:
    from fieldwork_agent.core.config import Config
    from fieldwork_agent.kernel.repository.protocol import ProjectRepository

SERVER_NAME = "fieldwork-agent"
SERVER_VERSION = "0.1.0"


def _text_payload(payload: dict) -> CallToolResult:
    """统一包络 dict → CallToolResult（单条 JSON 文本；业务失败也在文本里，不走协议异常）。"""
    return CallToolResult(content=[
        TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))])


def create_server(config: "Config | None" = None,
                  repo: "ProjectRepository | None" = None) -> LowLevelServer:
    """组装低层 MCP Server：22 tools（13 能力 + 9 状态）+ 资源面 + 2 prompts。

    - ``config=None``：无 LLM（文档能力自然模板降级）、FlyAIClient 默认实例
      （无 CLI/key 时搜索走 flyai_unavailable 体验降级）——L3 契约测试即此路径；
    - ``repo=None``：按 ``FA_STORAGE`` 经 ``make_repository`` 选后端（默认 json）。
    """
    cfg: Any = config
    if repo is None:
        from fieldwork_agent.core.config import Config
        from fieldwork_agent.kernel.repository.factory import make_repository
        cfg = config or Config()
        repo = make_repository(cfg)

    llm = None
    if cfg is not None and getattr(cfg, "dashscope_available", False):
        # 只在有 key 时构造：LLMClient 无 key 会抛 RuntimeError（kernel/llm 契约）
        from fieldwork_agent.kernel.llm import LLMClient
        llm = LLMClient(api_key=cfg.dashscope_api_key, models=cfg.llm_models)
    # cfg 为 None 也建默认 client：无 key 时搜索自然走 flyai_unavailable/体验降级
    client = FlyAIClient(api_key=cfg.flyai_api_key) if cfg is not None else FlyAIClient()
    # 宿主注入上下文：保留键 llm/client 的唯一来源（dispatch 信任边界）
    host_ctx = {"llm": llm, "client": client}

    # Task 2 设计：fn 签名显式声明 client 的能力（8 搜索 + 2 聚合）填入共享
    # client 版分派表；文档能力不入表（走 host_ctx["llm"] 注入）
    for cap_name, meta in capabilities().items():
        parameters = inspect.signature(meta.fn).parameters
        if dispatch._declares(parameters, "client"):   # dispatch 注释明许 server 组装时注入
            dispatch._CLIENTLESS_SINGLETONS[cap_name] = functools.partial(meta.fn, client=client)

    handlers = state_tools.build_handlers(repo)
    tools = capability_tools() + state_tools.STATE_TOOLS   # 13 + 9 = 22（单一来源，不手抄）

    srv = LowLevelServer(SERVER_NAME, version=SERVER_VERSION)

    async def _list_tools(ctx: ServerRequestContext,
                          params: PaginatedRequestParams | None) -> ListToolsResult:
        return ListToolsResult(tools=tools)

    async def _call_tool(ctx: ServerRequestContext,
                         params: CallToolRequestParams) -> CallToolResult:
        name = params.name
        arguments = params.arguments or {}
        if name in handlers:
            try:
                payload = handlers[name](**arguments)
            except Exception as exc:   # catch-all：patch 非 dict 的 TypeError、record 错类型
                # 的 ValidationError 等逃逸异常一律折包络（Task 3 评审移交），不崩协议层
                payload = {"ok": False, "error": {
                    "code": "internal_error", "message": f"状态工具执行异常: {exc}",
                    "hint": "对照该工具 inputSchema 检查参数形状"}}
        else:
            # 能力路径：阻塞的 flyai 子进程/文档生成丢工作线程；
            # call_capability 自带包络兜底，不重复包
            payload = await anyio.to_thread.run_sync(
                lambda: dispatch.call_capability(name, arguments, host_ctx))
        return _text_payload(payload)

    async def _list_resources(ctx: ServerRequestContext,
                              params: PaginatedRequestParams | None) -> ListResourcesResult:
        return ListResourcesResult(resources=res_mod.list_resources(repo))

    async def _read_resource(ctx: ServerRequestContext,
                             params: ReadResourceRequestParams) -> ReadResourceResult:
        out = res_mod.read_resource(repo, str(params.uri))
        if not out["ok"]:
            # 对齐 SDK MCPServer._handle_read_resource 错误路径：找不到 → INVALID_PARAMS
            raise MCPError(code=INVALID_PARAMS, message=out["error"]["message"],
                           data={"uri": str(params.uri)})
        return ReadResourceResult(contents=[TextResourceContents(
            uri=params.uri, mime_type="text/markdown", text=out["text"])])

    async def _list_prompts(ctx: ServerRequestContext,
                            params: PaginatedRequestParams | None) -> ListPromptsResult:
        return ListPromptsResult(prompts=prompts_mod.list_prompts())

    async def _get_prompt(ctx: ServerRequestContext,
                          params: GetPromptRequestParams) -> GetPromptResult:
        out = prompts_mod.get_prompt(params.name, params.arguments or {})
        if not out["ok"]:
            raise MCPError(code=INVALID_PARAMS, message=out["error"]["message"])
        # prompts 模块的 dict 消息 → SDK PromptMessage（content 为 TextContent 联合体）
        return GetPromptResult(
            description=out.get("description"),
            messages=[PromptMessage(role=m["role"],
                                    content=TextContent(type="text", text=m["content"]))
                      for m in out["messages"]])

    srv.add_request_handler("tools/list", PaginatedRequestParams, _list_tools)
    srv.add_request_handler("tools/call", CallToolRequestParams, _call_tool)
    srv.add_request_handler("resources/list", PaginatedRequestParams, _list_resources)
    srv.add_request_handler("resources/read", ReadResourceRequestParams, _read_resource)
    srv.add_request_handler("prompts/list", PaginatedRequestParams, _list_prompts)
    srv.add_request_handler("prompts/get", GetPromptRequestParams, _get_prompt)
    return srv


def run_stdio() -> None:
    """console script 入口：fieldwork-mcp（stdio 传输，SDK 2.3.0 实测形状）。

    1.x 样例里的 ``types.NotificationOptions`` 不存在；初始化选项由低层 Server 的
    ``create_initialization_options()`` 便利方法生成（capabilities 按已注册 handler 推导）。
    """
    import asyncio

    from mcp.server.stdio import stdio_server

    srv = create_server()

    async def _amain() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await srv.run(read_stream, write_stream, srv.create_initialization_options())

    asyncio.run(_amain())
