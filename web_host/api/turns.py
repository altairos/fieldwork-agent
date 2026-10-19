"""回合路由（R4）：创建/SSE 订阅/取消。"""
import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fieldwork_agent.kernel.sources.amap import AMapClient
from fieldwork_agent.kernel.sources.flyai import FlyAIClient
from fieldwork_agent.web_host.api.guard import checked_owned_project, checked_turn_id
from fieldwork_agent.web_host.api.owner import owner_from_request
from fieldwork_agent.web_host.deps import (
    get_amap_raw,
    get_flyai_raw,
    get_llm,
    get_repo,
    get_turn_manager,
)
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
# 高德 Web 服务 Key（N4 多租户）：与 MCP 侧 AMAP_KEY_HEADER 同名同义
AMAP_HEADER = "X-Amap-Key"


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
    - 飞猪可选：仅影响工具调用可用性，缺失时回落单例（可为空 key，工具内部降级）；
    - 高德可选（N4）：同飞猪语义，页面 key 构建回合级 AMapClient。
    返回 (llm, flyai, amap)，均为回合级实例，不写回 app.state。
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
    amap = get_amap_raw(request.app)
    amap_key = _header(request, AMAP_HEADER)
    if amap_key:
        amap = AMapClient(amap_key)  # 纯 key 构造无失败面（MCP 同惯例），不走 try/except
    return llm, flyai, amap


@router.get("/projects/{project_id}/messages")
async def list_messages(project_id: str, request: Request,
                        owner: str | None = Depends(owner_from_request)):
    """消息史恢复（刷新回填）：按时间序回 role/content 全量史。

    只回 role/content（ts/metadata 是内核内部细节，不进 API 面）；
    例外：assistant 消息携带快捷选项时回传 quick_reply_groups（多组
    带标签，前端渲染多行 chips）；旧史仅有扁平 quick_replies 时同时回传
    两者（quick_reply_groups 由扁平列表包成单个无标签组）；``limit=0`` 走全量。
    """
    repo = get_repo(request)
    checked_owned_project(project_id, repo, owner)
    messages = repo.get_messages(project_id, limit=0)
    out = []
    for m in messages:
        item = {"role": m["role"], "content": m["content"]}
        meta = m.get("metadata") or {}
        quick = meta.get("quick_replies")
        if quick:
            item["quick_replies"] = quick
        groups = meta.get("quick_reply_groups")
        if not groups and quick:
            groups = [{"label": "", "options": quick}]   # 旧史兼容：扁平列表包成单组
        if groups:
            item["quick_reply_groups"] = groups
        out.append(item)
    return out


@router.post("/projects/{project_id}/messages", status_code=201)
async def create_turn(project_id: str, body: CreateTurnRequest,
                      request: Request, background: BackgroundTasks,
                      owner: str | None = Depends(owner_from_request)):
    repo = get_repo(request)
    checked_owned_project(project_id, repo, owner)
    tm = get_turn_manager(request)
    if tm.is_writing(project_id):
        raise HTTPException(409, "该项目有正在执行的回合")
    llm, flyai, amap = _resolve_clients(request)
    turn = tm.create_turn(project_id, idempotency_key=body.idempotency_key)
    background.add_task(_execute_turn, request.app, project_id,
                        turn.turn_id, body.text, llm, flyai, amap)
    return {"turn_id": turn.turn_id, "status": turn.status}


async def _execute_turn(app, pid, tid, text, llm, flyai, amap):
    """后台任务：获取写锁 → 执行 agent loop → 释放（amap 为回合级实例，N4）。"""
    from fieldwork_agent.web_host.agent.loop import run_turn
    from fieldwork_agent.web_host.deps import get_repo_raw, get_tm_raw
    repo = get_repo_raw(app)
    tm = get_tm_raw(app)
    async with tm.acquire_write_lock(pid):
        await run_turn(repo, pid, tid, text, tm, llm, flyai, amap_client=amap)


async def _event_stream(tm, project_id: str, turn_id: str, last_id: int, handle):
    """SSE 事件流：回放已有事件 + 实时订阅新事件。

    先订阅再回放：消除「回放快照之后、订阅建立之前」窗口内落盘事件的
    丢失（回合恰在该窗口完成时 turn_done 会永久丢掉）；订阅早于回放
    会造成重叠，用回放末尾 seq 去重。
    """
    queue = tm.subscribe(project_id, turn_id)
    try:
        max_seq = last_id
        # 回放已有事件
        for env in tm.replay(project_id, turn_id, after_seq=last_id):
            yield encode_event(env)
            max_seq = max(max_seq, env.seq)
        # 回放后已终态：排空队列（窗口内落盘的事件 + 哨兵）再关闭；
        # 重连已完成回合时队列为空，同样立即关流
        if handle.status in TERMINAL_STATUSES:
            while not queue.empty():
                item = queue.get_nowait()
                if item is None:
                    return
                if item.seq > max_seq:
                    yield encode_event(item)
            return
        # 实时订阅新事件
        while True:
            try:
                item = await asyncio.wait_for(queue.get(),
                                              timeout=HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                yield encode_heartbeat()
                continue
            if item is None:
                break
            if item.seq <= max_seq:
                continue  # 订阅早于回放快照的重叠事件，去重
            yield encode_event(item)
    finally:
        tm.unsubscribe(project_id, turn_id, queue)


@router.get("/projects/{project_id}/turns/{turn_id}/events")
async def stream_events(project_id: str, turn_id: str, request: Request,
                        owner: str | None = Depends(owner_from_request)):
    repo = get_repo(request)
    checked_owned_project(project_id, repo, owner)
    checked_turn_id(turn_id)
    tm = get_turn_manager(request)
    handle = tm.get_turn(project_id, turn_id)
    if handle is None:
        raise HTTPException(404, "回合不存在")
    last_id = parse_last_event_id(dict(request.headers))
    return sse_response(_event_stream(tm, project_id, turn_id, last_id, handle))


@router.post("/projects/{project_id}/turns/{turn_id}/cancel")
async def cancel_turn(project_id: str, turn_id: str, request: Request,
                      owner: str | None = Depends(owner_from_request)):
    repo = get_repo(request)
    checked_owned_project(project_id, repo, owner)
    checked_turn_id(turn_id)
    tm = get_turn_manager(request)
    handle = tm.get_turn(project_id, turn_id)
    if handle is None:
        raise HTTPException(404, "回合不存在")
    tm.cancel(project_id, turn_id)
    return {"turn_id": turn_id, "status": handle.status}


@router.post("/projects/{project_id}/turns/{turn_id}/retry", status_code=201)
async def retry_turn(project_id: str, turn_id: str, request: Request,
                     background: BackgroundTasks,
                     owner: str | None = Depends(owner_from_request)):
    """重新生成：弹出末条 assistant 与 user，以同一文本新建回合。

    只允许终态回合且对话末尾必须是 [user, assistant]——先校验后弹出，
    任何一步失败都不改历史（否则弹完发现不可重试就丢消息）。
    """
    repo = get_repo(request)
    checked_owned_project(project_id, repo, owner)
    checked_turn_id(turn_id)
    tm = get_turn_manager(request)
    handle = tm.get_turn(project_id, turn_id)
    if handle is None:
        raise HTTPException(404, "回合不存在")
    if handle.status not in TERMINAL_STATUSES:
        raise HTTPException(409, "该回合仍在执行，无法重新生成")
    if tm.is_writing(project_id):
        raise HTTPException(409, "该项目有正在执行的回合")
    tail = repo.get_messages(project_id, limit=2)
    if len(tail) != 2 or tail[0]["role"] != "user" or tail[1]["role"] != "assistant":
        raise HTTPException(409, detail={
            "code": "retry_not_applicable",
            "message": "对话末尾不是「用户消息 + 助手回复」，无法重新生成",
        })
    llm, flyai, amap = _resolve_clients(request)
    repo.pop_last_message(project_id, role="assistant")
    text = repo.pop_last_message(project_id, role="user")["content"]
    turn = tm.create_turn(project_id)
    background.add_task(_execute_turn, request.app, project_id,
                        turn.turn_id, text, llm, flyai, amap)
    return {"turn_id": turn.turn_id, "status": turn.status}
