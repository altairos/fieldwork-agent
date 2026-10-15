"""FastAPI 入口：lifespan 管理单例、CORS(localhost)、路由挂载。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app(config=None, repo=None, web_dist=None) -> FastAPI:
    from fieldwork_agent.web_host.deps import init_deps

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_deps(app, config=config, repo=repo)
        yield

    app = FastAPI(title="Fieldwork Web Host", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    from fieldwork_agent.web_host.api import exports, projects, turns

    app.include_router(projects.router)
    app.include_router(turns.router)
    app.include_router(exports.router)

    # ---- 静态前端挂载（Task 10）：API 路由已注册，优先级高于 catch-all ----
    _mount_frontend(app, web_dist)
    return app


def _mount_frontend(app: FastAPI, web_dist=None) -> None:
    """挂载 web/dist 静态产物；dist 不存在则静默跳过（开发期/CI 未构建时不影响 API）。

    - /assets → StaticFiles（JS/CSS/字体等带哈希产物）
    - catch-all GET /{full_path} → SPA fallback：命中真实静态文件则返回该文件，
      否则返回 index.html（支持 BrowserRouter 深链如 /p/:id 刷新）。
    - 安全：resolve 后校验路径仍在 dist 内，防目录穿越。
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
            candidate = (dist / full_path).resolve()
            # 防目录穿越：candidate 必须在 dist 内且是文件
            if str(candidate).startswith(str(dist_resolved)) and candidate.is_file():
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
