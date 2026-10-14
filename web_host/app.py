"""FastAPI 入口：lifespan 管理单例、CORS(localhost)、路由挂载。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app(config=None, repo=None) -> FastAPI:
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
    return app


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
