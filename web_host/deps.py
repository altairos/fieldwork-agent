"""依赖注入：app.state 上挂 Config/Repository/TurnManager/LLM/FlyAI 单例。"""

from fastapi import Request


def init_deps(app, *, config=None, repo=None) -> None:
    from fieldwork_agent.core.config import Config
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.kernel.sources.flyai import FlyAIClient

    cfg = config or Config()
    if repo is None:
        repo = JsonRepository(projects_dir=cfg.projects_dir)
    app.state.config = cfg
    app.state.repo = repo
    app.state.flyai = FlyAIClient(getattr(cfg, "flyai_api_key", ""))
    app.state.turn_manager = None  # Task 3 创建后替换
    app.state.llm = None
    if getattr(cfg, "dashscope_available", False):
        from fieldwork_agent.kernel.llm import LLMClient

        try:
            app.state.llm = LLMClient(cfg.dashscope_api_key, cfg.llm_models)
        except RuntimeError:
            pass
    # R4：进程重启后遗留 running → interrupted（Task 3 创建 TurnManager 后启用）
    # pids = [p["project_id"] for p in repo.list_projects()]
    # app.state.turn_manager.recover_interrupted(pids)


def get_repo(request: Request):
    return request.app.state.repo


def get_config(request: Request):
    return request.app.state.config


def get_turn_manager(request: Request):
    return request.app.state.turn_manager


def get_llm(request: Request):
    return request.app.state.llm


# BackgroundTasks 无 Request，从 app 直取
def get_repo_raw(app):
    return app.state.repo


def get_tm_raw(app):
    return app.state.turn_manager


def get_flyai_raw(app):
    return app.state.flyai
