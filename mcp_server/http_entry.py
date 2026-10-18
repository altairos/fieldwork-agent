"""streamable HTTP 传输入口（改造1）：挂进 web_host /mcp 或独立起服。

选型（docs/research/managed-agents-integration.md）：
- ``StreamableHTTPSessionManager(stateless=True, json_response=True)``：每请求
  新 transport、无 GET SSE / 会话粘性，匹配百炼「每次调用代传动态 Header」；
- 顶部直接 import SDK 的 streamable_http_manager —— SDK 缺失时本模块 import
  即失败，web_host._mount_mcp 捕获 ImportError 后优雅降级（不挂 /mcp）。
"""
from contextlib import asynccontextmanager
from typing import Any

from fieldwork_agent.mcp_server import request_ctx
from fieldwork_agent.mcp_server.server import create_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager


class HeaderOverridesMiddleware:
    """纯 ASGI 中间件：动态 Header → contextvar。

    必须纯 ASGI、不能 BaseHTTPMiddleware：后者在独立任务里跑下游 app，
    set 的 contextvar 对工具调用链不可见；纯 ASGI 同任务直通。
    """

    # header 名（ASGI 恒小写）→ request_ctx 覆盖语义键
    _WANTED = {
        request_ctx.DASHSCOPE_KEY_HEADER: "dashscope_api_key",
        request_ctx.FLYAI_KEY_HEADER: "flyai_api_key",
        request_ctx.AMAP_KEY_HEADER: "amap_api_key",
    }

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        found: dict[str, str] = {}
        for raw_key, raw_value in scope.get("headers") or []:
            semantic = self._WANTED.get(raw_key.decode("latin-1").lower())
            if semantic:
                value = raw_value.decode("latin-1").strip()
                if value:
                    found[semantic] = value
        if not found:
            await self.app(scope, receive, send)
            return
        token = request_ctx.set_overrides(found)
        try:
            await self.app(scope, receive, send)
        finally:
            request_ctx.reset(token)


class StreamableHttpService:
    """create_server + stateless/json_response session manager 的薄封装。"""

    def __init__(self, config=None, repo=None):
        server = create_server(config, repo)
        self._manager = StreamableHTTPSessionManager(
            app=server, stateless=True, json_response=True
        )
        # 中间件包 handle_request：Header 覆盖在进 manager 前落 contextvar
        self.asgi = HeaderOverridesMiddleware(self._manager.handle_request)

    @asynccontextmanager
    async def run(self):
        """托管 manager lifespan（FastAPI lifespan / 独立 Starlette 均用）。"""
        async with self._manager.run():
            yield


def run_http() -> None:
    """console script：fieldwork-mcp-http —— 独立 streamable HTTP 服务。"""
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Route

    service = StreamableHttpService()

    @asynccontextmanager
    async def lifespan(_app):
        async with service.run():
            yield

    # Route 精确挂 “/mcp”（Mount 不匹配无尾斜杠的端点本身，理由见 web_host._mount_mcp）
    app = Starlette(
        lifespan=lifespan,
        routes=[Route("/mcp", endpoint=service.asgi, name="mcp",
                      methods=["GET", "POST", "DELETE"])],
    )
    uvicorn.run(app, host="127.0.0.1", port=8310)


if __name__ == "__main__":
    run_http()
