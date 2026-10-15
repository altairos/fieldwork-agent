"""回合路由（R4）：创建/SSE 订阅/取消。"""
import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fieldwork_agent.web_host.api.guard import checked_pid
from fieldwork_agent.web_host.deps import get_llm, get_repo, get_turn_manager
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


@router.post("/projects/{project_id}/messages", status_code=201)
async def create_turn(project_id: str, body: CreateTurnRequest,
                      request: Request, background: BackgroundTasks):
    repo = get_repo(request)
    tm = get_turn_manager(request)
    llm = get_llm(request)
    # 终审 C1（HTTP 侧）：守卫必须先于 repo/TurnManager——tm.create_turn 以
    # root/pid/turns/tid 拼路径，穿越 id 可在 projects 根外建目录写 meta.json。
    # turn_id 不在本次范围（客户端 turn_id 只能命中内存注册表的 uuid 键，见 guard-report）
    project_id = checked_pid(project_id, repo)
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}") from None
    if tm.is_writing(project_id):
        raise HTTPException(409, "该项目有正在执行的回合")
    turn = tm.create_turn(project_id, idempotency_key=body.idempotency_key)
    background.add_task(_execute_turn, request.app, project_id,
                        turn.turn_id, body.text, llm)
    return {"turn_id": turn.turn_id, "status": turn.status}


async def _execute_turn(app, pid, tid, text, llm):
    """后台任务：获取写锁 → 执行 agent loop → 释放。"""
    from fieldwork_agent.web_host.agent.loop import run_turn
    from fieldwork_agent.web_host.deps import get_flyai_raw, get_repo_raw, get_tm_raw
    repo = get_repo_raw(app)
    tm = get_tm_raw(app)
    flyai = get_flyai_raw(app)
    async with tm.acquire_write_lock(pid):
        await run_turn(repo, pid, tid, text, tm, llm, flyai)


@router.get("/projects/{project_id}/turns/{turn_id}/events")
async def stream_events(project_id: str, turn_id: str, request: Request):
    # 守卫先于 tm（tm.replay 以 root/pid/turns/tid 拼 events.jsonl 路径）
    project_id = checked_pid(project_id, get_repo(request))
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
    project_id = checked_pid(project_id, get_repo(request))
    tm = get_turn_manager(request)
    handle = tm.get_turn(project_id, turn_id)
    if handle is None:
        raise HTTPException(404, "回合不存在")
    tm.cancel(project_id, turn_id)
    return {"turn_id": turn_id, "status": handle.status}
