"""依赖注入：app.state 上挂 Config/Repository/TurnManager/LLM/FlyAI 单例。"""

from fastapi import Request


def init_deps(app, *, config=None, repo=None) -> None:
    from fieldwork_agent.core.config import Config
    from fieldwork_agent.kernel.repository.factory import make_repository
    from fieldwork_agent.kernel.sources.amap import AMapClient
    from fieldwork_agent.kernel.sources.flyai import FlyAIClient
    from fieldwork_agent.web_host.turn_manager import TurnManager

    cfg = config or Config()
    if repo is None:
        repo = make_repository(cfg)
    app.state.config = cfg
    app.state.repo = repo
    app.state.flyai = FlyAIClient(getattr(cfg, "flyai_api_key", ""))
    # AMapClient 同款单例：无 key 也构建（原语走 amap_invalid_key 体验降级不出网，
    # 与 MCP 组装期同款惯例）；N0 起 agent loop 经此通道注入 amap 原语
    app.state.amap = AMapClient(getattr(cfg, "amap_api_key", ""))
    app.state.turn_manager = TurnManager(root=cfg.projects_dir)
    from fieldwork_agent.kernel.repository.user_store_factory import make_user_store
    from fieldwork_agent.web_host.ratelimit import RateLimits

    app.state.user_store = make_user_store(cfg)
    app.state.rate_limits = RateLimits(cfg)
    app.state.llm = None
    if getattr(cfg, "dashscope_available", False):
        from fieldwork_agent.kernel.llm import LLMClient, plan_base_url

        try:
            app.state.llm = LLMClient(
                cfg.dashscope_api_key, cfg.llm_models,
                base_url=plan_base_url(getattr(cfg, "dashscope_plan", "")))
        except RuntimeError:
            # Q03：缺 SDK / 构建失败 → llm 保持 None，宿主以 403 llm_key_required
            # 或模板降级应对；启动不因此失败（本地无 key 也要能开页面）。
            pass
    # R4：进程重启后遗留 running → interrupted
    pids = [p["project_id"] for p in repo.list_projects()]
    app.state.turn_manager.recover_interrupted(pids)


def get_repo(request: Request):
    return request.app.state.repo


def get_config(request: Request):
    return request.app.state.config


def get_turn_manager(request: Request):
    return request.app.state.turn_manager


def get_llm(request: Request):
    return request.app.state.llm


def get_user_store(request: Request):
    return request.app.state.user_store


def get_rate_limits(request: Request):
    """限流器组；未经 init_deps 构造的 app 懒建一份（测试/嵌入式用法）。"""
    limits = getattr(request.app.state, "rate_limits", None)
    if limits is None:
        from fieldwork_agent.web_host.ratelimit import RateLimits

        limits = RateLimits(getattr(request.app.state, "config", None))
        request.app.state.rate_limits = limits
    return limits


# BackgroundTasks 无 Request，从 app 直取
def get_repo_raw(app):
    return app.state.repo


def get_tm_raw(app):
    return app.state.turn_manager


def get_flyai_raw(app):
    return app.state.flyai


def get_amap_raw(app):
    return app.state.amap
