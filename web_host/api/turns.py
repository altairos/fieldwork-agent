"""回合路由（R4）：创建/SSE 订阅/取消。"""
import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fieldwork_agent.kernel.sources.flyai import FlyAIClient
from fieldwork_agent.web_host.api.guard import checked_project_id, checked_turn_id
from fieldwork_agent.web_host.deps import get_flyai_raw, get_llm, get_repo, get_turn_manager
from fieldwork_agent.web_host.sse import (
    HEARTBEAT_INTERVAL,
    encode_event,
    encode_heartbeat,
    parse_last_event_id,
    sse_response,
)
from fieldwork_agent.web_host.turn_manager import TERMINAL_STATUSES
from pydantic import BaseModel

router = APIRouter(tags=["turns"])


class CreateTurnRequest(BaseModel):
    text: str
    idempotency_key: str | None = None


# 页面 key 请求头（首页「API 密钥」卡存 localStorage，rest.ts 统一注入）
DASHSCOPE_HEADER = "X-DashScope-Key"
FLYAI_HEADER = "X-FlyAI-Key"
# 套餐类型（coding/token）：套餐 Key（sk-sp-）配专属 OpenAI 兼容端点，白名单映射
DASHSCOPE_PLAN_HEADER = "X-DashScope-Plan"


def _header(request: Request, name: str) -> str:
    return (request.headers.get(name) or "").strip()


def _build_llm(api_key: str, request: Request):
    """按页面 key 构建回合级 LLMClient（模块级工厂，测试可 monkeypatch）。"""
    from fieldwork_agent.kernel.llm import LLMClient, plan_base_url

    base_url = plan_base_url(_header(request, DASHSCOPE_PLAN_HEADER))
    return LLMClient(api_key, request.app.state.config.llm_models, base_url)


def _resolve_clients(request: Request):
    """key 解析：页面 key（请求头）优先 → 启动单例；百炼两者皆无 → 403。

    - 百炼必填：无页面 key 且单例 None（服务端未配）→ 403 llm_key_required，
      不创建回合、不进入对话（不再降级为固定文案）；
    - 飞猪可选：仅影响工具调用可用性，缺失时回落单例（可为空 key，工具内部降级）。
    返回 (llm, flyai)，均为回合级实例，不写回 app.state。
    """
    llm = get_llm(request)
    page_key = _header(request, DASHSCOPE_HEADER)
    if page_key:
        try:
            llm = _build_llm(page_key, request)
        except RuntimeError as exc:
            raise HTTPException(500, f"LLM 客户端初始化失败: {exc}") from None
    if llm is None:
        raise HTTPException(403, detail={
            "code": "llm_key_required",
            "message": "未配置百炼 API Key：请在首页「API 密钥」卡填写并保存，"
                       "或以环境变量 DASHSCOPE_API_KEY 启动服务端",
        })
    flyai = get_flyai_raw(request.app)
    flyai_key = _header(request, FLYAI_HEADER)
    if flyai_key:
        flyai = FlyAIClient(flyai_key)
    return llm, flyai


@router.post("/projects/{project_id}/messages", status_code=201)
async def create_turn(project_id: str, body: CreateTurnRequest,
                      request: Request, background: BackgroundTasks):
    repo = get_repo(request)
    checked_project_id(project_id, repo)
    tm = get_turn_manager(request)
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
    if tm.is_writing(project_id):
        raise HTTPException(409, "该项目有正在执行的回合")
    llm, flyai = _resolve_clients(request)
    turn = tm.create_turn(project_id, idempotency_key=body.idempotency_key)
    background.add_task(_execute_turn, request.app, project_id,
                        turn.turn_id, body.text, llm, flyai)
    return {"turn_id": turn.turn_id, "status": turn.status}


async def _execute_turn(app, pid, tid, text, llm, flyai):
    """后台任务：获取写锁 → 执行 agent loop → 释放。"""
    from fieldwork_agent.web_host.agent.loop import run_turn
    from fieldwork_agent.web_host.deps import get_repo_raw, get_tm_raw
    repo = get_repo_raw(app)
    tm = get_tm_raw(app)
    async with tm.acquire_write_lock(pid):
        await run_turn(repo, pid, tid, text, tm, llm, flyai)


@router.get("/projects/{project_id}/turns/{turn_id}/events")
async def stream_events(project_id: str, turn_id: str, request: Request):
    repo = get_repo(request)
    checked_project_id(project_id, repo)
    checked_turn_id(turn_id)
    tm = get_turn_manager(request)
    handle = tm.get_turn(project_id, turn_id)
    if handle is None:
        raise HTTPException(404, "回合不存在")
    last_id = parse_last_event_id(dict(request.headers))

    async def generator():
        # 回放已有事件
        for env in tm.replay(project_id, turn_id, after_seq=last_id):
            yield encode_event(env)
        # 已结束 → 直接关闭
        if handle.status in TERMINAL_STATUSES:
            return
        # 实时订阅
        queue = tm.subscribe(project_id, turn_id)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(),
                                                  timeout=HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    yield encode_heartbeat()
                    continue
                if item is None:
                    break
                yield encode_event(item)
        finally:
            tm.unsubscribe(project_id, turn_id, queue)

    return sse_response(generator())


@router.post("/projects/{project_id}/turns/{turn_id}/cancel")
async def cancel_turn(project_id: str, turn_id: str, request: Request):
    repo = get_repo(request)
    checked_project_id(project_id, repo)
    checked_turn_id(turn_id)
    tm = get_turn_manager(request)
    handle = tm.get_turn(project_id, turn_id)
    if handle is None:
        raise HTTPException(404, "回合不存在")
    tm.cancel(project_id, turn_id)
    return {"turn_id": turn_id, "status": handle.status}
