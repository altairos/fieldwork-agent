"""FastAPI 入口：lifespan 管理单例、CORS(localhost)、路由挂载。"""

import hmac
import os
import shutil
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

APP_TOKEN_HEADER = "X-App-Token"
# 受保护前缀（审计 F02 + MCP 改造2）：/projects、/settings、/mcp 全族；
# /health、/ready 与静态前端豁免——页面要先能打开、健康探针不该被令牌拦住。
_PROTECTED_PREFIXES = ("/projects", "/settings", "/mcp")


def _is_protected(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _PROTECTED_PREFIXES)


def _app_token_ok(request: Request) -> bool:
    """未配置令牌 → 完全开放（本地开发零影响）；配置则常量时间比对防时序探测。"""
    cfg = getattr(request.app.state, "config", None)
    expected = getattr(cfg, "app_token", "") or ""
    if not expected:
        return True
    supplied = request.headers.get(APP_TOKEN_HEADER, "")
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def create_app(config=None, repo=None, web_dist=None) -> FastAPI:
    from fieldwork_agent.web_host.deps import init_deps

    # 提前解析 cfg/repo：MCP /mcp 挂载与 Web API 必须共享同一 repo 实例
    # （JsonRepository 进程内缓存 / Sqlite 连接），lifespan 里不再各建一份
    cfg = config
    if cfg is None:
        from fieldwork_agent.core.config import Config
        cfg = Config()
    shared_repo = repo
    if shared_repo is None:
        from fieldwork_agent.kernel.repository.factory import make_repository
        shared_repo = make_repository(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_deps(app, config=cfg, repo=shared_repo)
        # /mcp 的 session manager 需 lifespan 托管（SDK 契约：run() 只能进一次）
        mcp_service = getattr(app.state, "mcp_service", None)
        if mcp_service is not None:
            async with mcp_service.run():
                yield
        else:
            yield

    app = FastAPI(title="Fieldwork Web Host", version="0.1.0", lifespan=lifespan)

    # F02 App Token 守卫：代码顺序必须先于 add_middleware(CORSMiddleware)——
    # Starlette 的 add_middleware 是 insert(0)，代码中后注册者位于栈外层；
    # 令牌守卫留在 CORS 内层，401 响应才带 CORS 头、预检 OPTIONS 由 CORS 应答不被拦死。
    @app.middleware("http")
    async def app_token_guard(request: Request, call_next):
        if _is_protected(request.url.path) and not _app_token_ok(request):
            return JSONResponse(
                status_code=401,
                content={
                    "code": "app_token_required",
                    "message": "缺少或错误的 X-App-Token 请求头：请在首页「API 密钥」卡填写访问令牌",
                },
            )
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health(request: Request):
        cfg = getattr(request.app.state, "config", None)
        return {"status": "ok", "auth_required": bool(getattr(cfg, "app_token", "") or "")}

    @app.get("/ready")
    async def ready(request: Request):
        """F15：区分「页面在线」（/health）与「可对外服务」（本端点）。

        blocking 项失败 → 503 not_ready；信息项（密钥配置）只报告不阻断——
        未配 key 时对话会按回合报 llm_key_required，不算服务不可用。
        """
        checks: dict[str, bool] = {}
        checks["flyai_cli"] = shutil.which("flyai") is not None
        repo = getattr(request.app.state, "repo", None)
        root = getattr(repo, "root", None)
        writable = False
        if root is not None:
            # 探针文件名每次唯一（终审#2）：固定名在并发 /ready 下会 touch→unlink 互踩，
            # 后到的一方 unlink 抛 FileNotFoundError（⊂ OSError）误报不可写 → 假 503。
            # 不用 monotonic_ns：Windows 粒度约 15.6ms，同进程两次请求可能同名（实测复现），
            # uuid4 才能保证唯一。
            probe = root / f".ready_probe.{os.getpid()}.{uuid.uuid4().hex}"
            try:
                probe.touch()
                probe.unlink()
                writable = True
            except OSError:
                writable = False
        checks["projects_dir_writable"] = writable
        cfg = getattr(request.app.state, "config", None)
        checks["dashscope_configured"] = getattr(request.app.state, "llm", None) is not None
        checks["flyai_key_configured"] = bool(getattr(cfg, "flyai_api_key", "") or "")
        # N6 软检查：JS Key 缺失只降级地图面板（文字卡片），不算服务不可用
        checks["amap_js_key_configured"] = bool(getattr(cfg, "amap_js_key", "") or "")
        blocking = ("flyai_cli", "projects_dir_writable")
        ok = all(checks[k] for k in blocking)
        return JSONResponse(
            content={"status": "ready" if ok else "not_ready", "checks": checks},
            status_code=200 if ok else 503,
        )

    from fieldwork_agent.web_host.api import exports, projects, settings, state, turns

    app.include_router(projects.router)
    app.include_router(turns.router)
    app.include_router(exports.router)
    app.include_router(settings.router)
    app.include_router(state.router)

    # ---- MCP streamable HTTP（改造1/2）：必须在 SPA catch-all 之前挂载 ----
    _mount_mcp(app, config=cfg, repo=shared_repo)

    # ---- 静态前端挂载（Task 10）：API 路由已注册，优先级高于 catch-all ----
    _mount_frontend(app, web_dist)
    return app


def _mount_mcp(app: FastAPI, config=None, repo=None) -> None:
    """挂 MCP streamable HTTP 端点到 /mcp（改造1/2，docs/research）。

    - SDK 缺失（未装 ``mcp`` extra）→ http_entry import 即失败，此处静默降级
      不挂载：Web 宿主其余功能完全不受影响（改造2 的降级承诺）；
    - ``StreamableHTTPSessionManager.run()`` 只能进一次（SDK 契约），由
      create_app 的 lifespan 经 ``app.state.mcp_service`` 托管；
    - 挂载顺序必须在 ``_mount_frontend``（SPA catch-all GET）之前，否则
      POST /mcp 被吞成 405。
    """
    try:
        from fieldwork_agent.mcp_server.http_entry import StreamableHttpService
    except ImportError:
        return
    service = StreamableHttpService(config=config, repo=repo)
    # 不能用 app.mount：新版 Starlette 的 Mount 按 path+"/{path:path}" 编译，
    # Mount("/mcp") 只匹配 "/mcp/<sub>" 而不匹配精确 "/mcp"（MCP 客户端
    # POST 的就是端点本身，会漏到 SPA catch-all 变 405）。Route 精确挂载
    # ASGI app，方法面全放行、语义交 SDK 自答（GET 探流/DELETE 会话终止）。
    # 此刻 catch-all 尚未注册，append 顺序天然在前。
    from starlette.routing import Route

    app.router.routes.append(
        Route("/mcp", endpoint=service.asgi, name="mcp",
              methods=["GET", "POST", "DELETE"])
    )
    app.state.mcp_service = service


def _mount_frontend(app: FastAPI, web_dist=None) -> None:
    """挂载 web/dist 静态产物；dist 不存在则静默跳过（开发期/CI 未构建时不影响 API）。

    - /assets → StaticFiles（JS/CSS/字体等带哈希产物）
    - catch-all GET /{full_path} → SPA fallback：命中真实静态文件则返回该文件，
      否则返回 index.html（支持 BrowserRouter 深链如 /p/:id 刷新）。
    - 安全：resolve 后校验路径仍在 dist 内，防目录穿越；resolve 本身对畸形路径
      （如含 NUL 字节的 /%00）会抛 ValueError/OSError，一并落回 index.html 而非 500。
    """
    from pathlib import Path

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    if web_dist is not None:
        dist = Path(web_dist)
    else:
        # web_host/app.py → 仓库根（package-dir 映射 fieldwork_agent=.）→ web/dist
        dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        return  # 未构建：只跑 API，不挂载（Python 测试/开发期常态）

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    dist_resolved = dist.resolve()

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path:
            try:
                candidate = (dist / full_path).resolve()
            except (ValueError, OSError):
                # 畸形路径（NUL 字节 / 平台不接受的字符）：不 500，落回 SPA 入口
                return FileResponse(index)
            # 防目录穿越：candidate 必须严格在 dist 内且是文件
            # （用 is_relative_to 而非 startswith，避免 dist_backup 等同前缀兄弟目录误判）
            if candidate.is_relative_to(dist_resolved) and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index)


def main():
    """console script 入口：fieldwork-web。"""
    import uvicorn

    uvicorn.run(
        "fieldwork_agent.web_host.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=8300,
        reload=False,
    )
