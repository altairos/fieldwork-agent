# Fieldwork Web 宿主（Plan 3/5）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 FastAPI Web 宿主——进程内直调 kernel 能力、async 工具回合循环（流式 LLM + 并行工具）、SSE 事件推送、R4 回合生命周期协议（turn_id 分离/幂等/序号/状态机/回放/取消/单写者），为 Plan 4 前端提供全部 REST + SSE 端点。

**Architecture:** Web 宿主是 kernel 之上的第二个薄壳（与 MCP Server 并列，spec §0 做法 A）。进程内直调 kernel 能力函数（不走 MCP 协议），共享 `runtime/loop.py` 的摘要逻辑但自建 async 循环以支持流式与并行。回合管理独立于 agent loop：TurnManager 负责 R4 生命周期（创建/持久化/回放/取消/写锁），agent loop 只负责"执行一回合并发射事件"。

**Tech Stack:** Python ≥3.10、FastAPI ≥0.111、uvicorn（ASGI）、pydantic v2、asyncio（并行工具 via `asyncio.to_thread`）、httpx（测试客户端）、pytest + pytest-asyncio（全 mock，不联网，无 key）。

**Spec:** `docs/superpowers/specs/2026-10-14-fieldwork-agent-mcp-kernel-design.md` §3（全节）/ §1.4（模型分档）/ §6 L3③（Web loop 契约）/ §6 R6（业务边界）。

**前置:** Plan 1 已完成（kernel 13 能力 + runtime/loop.py + JsonRepository）；Plan 2 已完成或并行收口（MCP Server 薄壳，本计划不依赖其代码但共享 kernel registry）。

**后续计划（本计划不含）:** React 前端（Plan 4）、SQLite 演进（Plan 5）。

## Global Constraints

- 依赖方向：`web_host/` 可 import `kernel/`、`runtime/`、`core.config`；kernel **不得** import web_host/fastapi/uvicorn（spec §1 单向依赖）。
- 返回包络：能力调用结果统一 `{ok, data?, error?: {code, message, hint}, degraded?}`；flyai 缺失 → `ok:false, code:"flyai_unavailable"`，不抛 HTTP 500。
- SSE 事件协议（spec §3.4 六事件）：`message_delta`/`tool_started`/`tool_completed`/`phase_transition`/`turn_done`/`error`；每事件带单调递增 `seq`（R4）+ `turn_id`。
- R4 回合协议逐条落实：创建与订阅分离（POST → turn_id，GET → SSE）；幂等键；事件序号；状态机 `running/completed/failed/cancelled/interrupted`；即时持久化；取消=停调度不改状态；单项目单写回合；进程重启遗留 → interrupted。
- 回合预算三重上限（R5，复用 runtime/loop.py 语义）：`max_iters=8`、`max_tool_calls=20`、`max_wall_seconds=60`。
- 工具结果进 LLM 消息用结构化摘要 ≤2000 字符（R5，复用 `runtime.loop._tool_content`）。
- 阶段推进确定性（`check_advance` 纯函数），绝不由 LLM 宣布（spec §3.3）。
- 测试全 mock：不联网、不真实调 flyai/DashScope、无 key；repo 用 tmp_path。
- ruff `E,F,I,UP,B`；行宽 100；注释中文；标识符英文。
- Windows git 用 `& "C:\Program Files\Git\cmd\git.exe"`。
- 包注册：`web_host/` 及其子包需在 `pyproject.toml [tool.setuptools] packages` 登记。

## 文件结构

```
web_host/
├── __init__.py           # docstring + create_app 再导出
├── app.py                # FastAPI 入口：lifespan/CORS/路由挂载/静态(Plan4)
├── deps.py               # 依赖注入单例：Config/Repository/TurnManager/LLM/FlyAI
├── events.py             # SSE 事件 pydantic 模型（6 事件 + EventEnvelope 信封）
├── sse.py                # SSE 编码/心跳/Last-Event-ID 解析/StreamingResponse 工厂
├── turn_manager.py       # R4 回合生命周期：状态机/幂等/写锁/事件持久化/回放
├── agent/
│   ├── __init__.py
│   ├── loop.py           # async 工具回合循环（流式 LLM + 并行工具 + 事件发射）
│   ├── dispatch.py       # 能力分派表 + asyncio.to_thread 并行执行
│   └── context.py        # system prompt + 项目快照 + 历史消息组装
└── api/
    ├── __init__.py
    ├── projects.py       # POST/GET /projects · GET /projects/{id}
    ├── turns.py          # POST /projects/{id}/messages · GET .../turns/{tid}/events · POST .../cancel
    └── exports.py        # GET /projects/{id}/proposal · GET .../proposal/download
tests/
├── test_web_sse.py
├── test_web_turn_manager.py
├── test_web_projects_api.py
├── test_web_agent_loop.py
├── test_web_turns_api.py
├── test_web_exports_api.py
└── test_web_contract.py      # L3③：全链路回合契约（fake LLM + fake flyai）
```

## API 端点总览（Plan 4 前端消费面）

| 方法 | 路径 | 用途 | 响应 |
|------|------|------|------|
| POST | `/projects` | 创建项目 | `{project_id, phase}` |
| GET | `/projects` | 项目列表 | `[{project_id, phase, updated_at, ...}]` |
| GET | `/projects/{id}` | 项目快照 | 完整 state + research_context + sites + informants |
| POST | `/projects/{id}/messages` | 创建回合 | `{turn_id, status:"running"}` |
| GET | `/projects/{id}/turns/{tid}/events` | SSE 事件流 | `text/event-stream`（支持 Last-Event-ID） |
| POST | `/projects/{id}/turns/{tid}/cancel` | 取消回合 | `{turn_id, status:"cancelled"}` |
| GET | `/projects/{id}/proposal` | 最新提案 | `{markdown, kind, version, sections}` |
| GET | `/projects/{id}/proposal/download` | 下载 .md | `text/markdown` attachment |
| GET | `/projects/{id}/proposals` | 全部版本列表 | `[{id, kind, version, created_at}]` |
| GET | `/health` | 健康检查 | `{status:"ok"}` |

## SSE 事件信封格式（R4 序号 + spec §3.4 六事件）

```
id: {seq}
event: {event_type}
data: {"turn_id":"...","seq":N,...payload}

```

每条 SSE 帧：`id` 为单调递增序号（Last-Event-ID 回放锚点）；`event` 为六事件名之一；`data` 为 JSON（含 turn_id + seq + 事件 payload）。心跳为 `: heartbeat\n\n` 注释帧（不触发前端事件处理）。

## R4 回合协议落实清单

| R4 要求 | 落实位置 |
|---------|---------|
| 创建与订阅分离 | Task 7: POST → turn_id (JSON)；GET → SSE |
| 幂等键 | Task 3: TurnManager.create_turn(idempotency_key) |
| 事件单调序号 | Task 3: append_event 分配 seq；Task 2: SSE id 字段 |
| 状态机 5 态 | Task 3: TurnHandle.status + TERMINAL_STATUSES |
| 事件持久化+回放 | Task 3: events.jsonl + replay(after_seq) |
| 即时持久化 | Task 6: run_turn 中用户消息/工具结果即时写 repo |
| 取消=停调度 | Task 6: cancel_event 检查；Task 7: cancel 端点 |
| 单项目单写回合 | Task 3: acquire_write_lock；Task 7: is_writing → 409 |
| 进程重启→interrupted | Task 3: deps.py 中 recover_interrupted |
| 事件保留期限 | Task 3: meta.json 记 created_at；清理策略留 Plan 5 |

---

### Task 1: 工程脚手架 + FastAPI app + 依赖注入

**Files:**
- Modify: `pyproject.toml`（dependencies + packages + console script）
- Create: `web_host/__init__.py`, `web_host/app.py`, `web_host/deps.py`, `web_host/api/__init__.py`, `web_host/api/projects.py`(占位), `web_host/api/turns.py`(占位), `web_host/api/exports.py`(占位), `web_host/agent/__init__.py`
- Test: `tests/test_web_projects_api.py`（本 Task 仅 health 端点）

**Interfaces:**
- Consumes: `core.config.Config`（projects_dir / dashscope_available / flyai_api_key / llm_models）
- Produces: `web_host.app.create_app(config: Config | None = None, repo: ProjectRepository | None = None) -> FastAPI`；`web_host.deps.get_repo(request) -> ProjectRepository`、`get_config(request)`、`get_turn_manager(request)`、`get_llm(request)`；console script `fieldwork-web = "fieldwork_agent.web_host.app:main"`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web_projects_api.py
import pytest
from httpx import ASGITransport, AsyncClient

from fieldwork_agent.web_host.app import create_app


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    return create_app(config=None, repo=repo)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_web_projects_api.py -v` → ModuleNotFoundError

- [ ] **Step 3: 实现**

`pyproject.toml` 修改：
- `dependencies` 追加 `"fastapi>=0.111"`, `"uvicorn[standard]>=0.30"`
- `[project.optional-dependencies]` dev 追加 `"httpx>=0.27"`
- `[project.scripts]` 追加 `fieldwork-web = "fieldwork_agent.web_host.app:main"`
- `packages` 追加 `"fieldwork_agent.web_host"`, `"fieldwork_agent.web_host.agent"`, `"fieldwork_agent.web_host.api"`

安装：`pip install -e ".[dev,mcp]"`

```python
# web_host/__init__.py
"""田野罗盘 Web 宿主（FastAPI）— kernel 之上的第二薄壳（spec §3）。"""
from fieldwork_agent.web_host.app import create_app

__all__ = ["create_app"]
```

```python
# web_host/app.py
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
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    from fieldwork_agent.web_host.api import projects, turns, exports
    app.include_router(projects.router)
    app.include_router(turns.router)
    app.include_router(exports.router)
    return app


def main():
    """console script 入口：fieldwork-web。"""
    import uvicorn
    uvicorn.run("fieldwork_agent.web_host.app:create_app", factory=True,
                host="127.0.0.1", port=8300, reload=False)
```

```python
# web_host/deps.py
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
    app.state.turn_manager = None   # Task 3 创建后替换
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
```

API 占位文件（`web_host/api/__init__.py`、`projects.py`、`turns.py`、`exports.py`）各建空 `router = APIRouter()`。`web_host/agent/__init__.py` 空 docstring。

- [ ] **Step 4: 确认通过** — `pytest tests/test_web_projects_api.py::test_health -v` → PASS
- [ ] **Step 5: Commit** — `& "C:\Program Files\Git\cmd\git.exe" add web_host tests/test_web_projects_api.py pyproject.toml; & "C:\Program Files\Git\cmd\git.exe" commit -m "feat(web): FastAPI脚手架+依赖注入+health端点"`

---

### Task 2: SSE 基础设施 + 事件模型

**Files:**
- Create: `web_host/events.py`, `web_host/sse.py`
- Test: `tests/test_web_sse.py`

**Interfaces:**
- Produces:
  - `events.EventEnvelope(turn_id: str, seq: int, event: str, data: dict)`
  - `events.MessageDelta(text: str)` / `ToolStarted(call_id, tool, args, purpose)` / `ToolCompleted(call_id, ok, data, degraded)` / `PhaseTransition(from_phase, to_phase)` / `TurnDone(reply_id, usage)` / `ErrorEvent(code, message, hint)`
  - `sse.encode_event(envelope: EventEnvelope) -> str`
  - `sse.encode_heartbeat() -> str`
  - `sse.parse_last_event_id(headers: dict) -> int`
  - `sse.sse_response(generator: AsyncIterator[str]) -> StreamingResponse`
  - `sse.HEARTBEAT_INTERVAL: int = 15`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web_sse.py
import json

from fieldwork_agent.web_host.events import EventEnvelope, MessageDelta, ToolStarted
from fieldwork_agent.web_host.sse import encode_event, encode_heartbeat, parse_last_event_id


def test_encode_event_sse_format():
    env = EventEnvelope(turn_id="t1", seq=7, event="message_delta",
                        data=MessageDelta(text="你好").model_dump())
    frame = encode_event(env)
    assert frame.startswith("id: 7\n")
    assert "event: message_delta\n" in frame
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["turn_id"] == "t1" and payload["seq"] == 7 and payload["text"] == "你好"
    assert frame.endswith("\n\n")


def test_heartbeat_is_comment():
    assert encode_heartbeat() == ": heartbeat\n\n"


def test_parse_last_event_id():
    assert parse_last_event_id({"last-event-id": "42"}) == 42
    assert parse_last_event_id({}) == 0
    assert parse_last_event_id({"last-event-id": "abc"}) == 0


def test_tool_started_payload():
    ev = ToolStarted(call_id="c1", tool="search_hotels",
                     args={"dest_name": "大理"}, purpose="住宿搜索")
    assert ev.model_dump()["call_id"] == "c1"
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_web_sse.py -v`

- [ ] **Step 3: 实现**

```python
# web_host/events.py
"""SSE 事件 pydantic 模型（spec §3.4 六事件 + R4 信封）。"""
from typing import Any

from pydantic import BaseModel, Field


class MessageDelta(BaseModel):
    text: str = ""

class ToolStarted(BaseModel):
    call_id: str
    tool: str
    args: dict = Field(default_factory=dict)
    purpose: str = ""

class ToolCompleted(BaseModel):
    call_id: str
    ok: bool = True
    data: Any = None
    degraded: str | None = None

class PhaseTransition(BaseModel):
    from_phase: str = Field(alias="from")
    to_phase: str = Field(alias="to")
    model_config = {"populate_by_name": True}

class TurnDone(BaseModel):
    reply_id: str = ""
    usage: dict = Field(default_factory=dict)

class ErrorEvent(BaseModel):
    code: str
    message: str
    hint: str = ""

class EventEnvelope(BaseModel):
    """统一信封：turn_id + 单调序号 + 事件名 + payload。"""
    turn_id: str
    seq: int
    event: str
    data: dict = Field(default_factory=dict)
```

```python
# web_host/sse.py
"""SSE 编码/心跳/Last-Event-ID 解析/StreamingResponse 工厂。"""
import json
from collections.abc import AsyncIterator

from fastapi.responses import StreamingResponse

from fieldwork_agent.web_host.events import EventEnvelope

MEDIA_TYPE = "text/event-stream"
HEARTBEAT_INTERVAL = 15


def encode_event(envelope: EventEnvelope) -> str:
    payload = {"turn_id": envelope.turn_id, "seq": envelope.seq, **envelope.data}
    data_line = json.dumps(payload, ensure_ascii=False, default=str)
    return f"id: {envelope.seq}\nevent: {envelope.event}\ndata: {data_line}\n\n"


def encode_heartbeat() -> str:
    return ": heartbeat\n\n"


def parse_last_event_id(headers: dict) -> int:
    raw = headers.get("last-event-id", "0")
    try:
        return max(0, int(raw))
    except (ValueError, TypeError):
        return 0


def sse_response(generator: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(generator, media_type=MEDIA_TYPE,
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})
```

- [ ] **Step 4: 确认通过** — `pytest tests/test_web_sse.py -v`
- [ ] **Step 5: Commit** — `& "C:\Program Files\Git\cmd\git.exe" add web_host/events.py web_host/sse.py tests/test_web_sse.py; & "C:\Program Files\Git\cmd\git.exe" commit -m "feat(web): SSE事件模型与编码基础设施"`

---

### Task 3: 回合管理器（R4 生命周期核心）

**Files:**
- Create: `web_host/turn_manager.py`
- Modify: `web_host/deps.py`（init_deps 中 import TurnManager 并替换 None 占位；追加 recover_interrupted 调用）
- Test: `tests/test_web_turn_manager.py`

**Interfaces:**
- Consumes: `web_host.events.EventEnvelope`
- Produces:
  - `TurnManager(root: Path)`
  - `TurnManager.create_turn(pid, idempotency_key=None) -> TurnHandle`
  - `TurnManager.get_turn(pid, tid) -> TurnHandle | None`
  - `TurnManager.append_event(pid, tid, event: str, data: dict) -> EventEnvelope`
  - `TurnManager.replay(pid, tid, after_seq: int) -> list[EventEnvelope]`
  - `TurnManager.finish(pid, tid, status: str) -> None`
  - `TurnManager.cancel(pid, tid) -> bool`
  - `TurnManager.is_writing(pid) -> bool`
  - `TurnManager.acquire_write_lock(pid) -> AsyncContextManager`
  - `TurnManager.subscribe(pid, tid) -> asyncio.Queue`
  - `TurnManager.unsubscribe(pid, tid, queue) -> None`
  - `TurnManager.recover_interrupted(pid_list: list[str]) -> None`
  - `TurnHandle` dataclass: `turn_id, pid, status, idempotency_key, created_at, cancel_event, seq_counter`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web_turn_manager.py
import asyncio

import pytest

from fieldwork_agent.web_host.turn_manager import TurnManager


@pytest.fixture
def tm(tmp_path):
    return TurnManager(root=tmp_path / "projects")


def test_create_turn_and_idempotency(tm):
    t1 = tm.create_turn("p1", idempotency_key="key-a")
    t2 = tm.create_turn("p1", idempotency_key="key-a")
    assert t1.turn_id == t2.turn_id
    t3 = tm.create_turn("p1", idempotency_key="key-b")
    assert t3.turn_id != t1.turn_id


def test_append_event_increments_seq(tm):
    t = tm.create_turn("p1")
    e1 = tm.append_event("p1", t.turn_id, "message_delta", {"text": "a"})
    e2 = tm.append_event("p1", t.turn_id, "message_delta", {"text": "b"})
    assert e1.seq == 1 and e2.seq == 2


def test_replay_after_seq(tm):
    t = tm.create_turn("p1")
    for i in range(5):
        tm.append_event("p1", t.turn_id, "message_delta", {"text": str(i)})
    events = tm.replay("p1", t.turn_id, after_seq=3)
    assert [e.seq for e in events] == [4, 5]


def test_finish_sets_terminal(tm):
    t = tm.create_turn("p1")
    tm.finish("p1", t.turn_id, "completed")
    assert tm.get_turn("p1", t.turn_id).status == "completed"


def test_cancel(tm):
    t = tm.create_turn("p1")
    assert tm.cancel("p1", t.turn_id) is True
    assert t.cancel_event.is_set() and t.status == "cancelled"


def test_write_lock_serializes(tm):
    async def _run():
        order = []
        async with tm.acquire_write_lock("p1"):
            order.append("a")
            await asyncio.sleep(0.01)
        async with tm.acquire_write_lock("p1"):
            order.append("b")
        return order
    assert asyncio.run(_run()) == ["a", "b"]


def test_subscribe_receives_events(tm):
    async def _run():
        t = tm.create_turn("p1")
        q = tm.subscribe("p1", t.turn_id)
        tm.append_event("p1", t.turn_id, "message_delta", {"text": "hi"})
        tm.finish("p1", t.turn_id, "completed")
        got = await asyncio.wait_for(q.get(), timeout=1)
        assert got.seq == 1
        sentinel = await asyncio.wait_for(q.get(), timeout=1)
        assert sentinel is None
    asyncio.run(_run())


def test_recover_interrupted(tm):
    t = tm.create_turn("p1")
    tm.recover_interrupted(["p1"])
    assert tm.get_turn("p1", t.turn_id).status == "interrupted"


def test_is_writing(tm):
    async def _run():
        assert tm.is_writing("p1") is False
        async with tm.acquire_write_lock("p1"):
            assert tm.is_writing("p1") is True
    asyncio.run(_run())
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_web_turn_manager.py -v`

- [ ] **Step 3: 实现** — `web_host/turn_manager.py` 核心设计：

持久化结构：`{root}/{pid}/turns/{tid}/meta.json`（status/idempotency_key/created_at）+ `events.jsonl`（每行一个 EventEnvelope JSON）。内存注册表 `_turns: dict[str, TurnHandle]`（键 `"{pid}:{tid}"`）；幂等索引 `_idempotency: dict[str, str]`（键 `"{pid}:{key}"` → tid）；写锁 `_locks: dict[str, asyncio.Lock]`；订阅 `_subscribers: dict[str, list[Queue]]`。

关键行为：
- `create_turn`：查幂等索引 → 命中则返回已有 handle；否则 uuid4 生成 tid、写 meta.json、注册内存。
- `append_event`：seq_counter += 1 → 构造 EventEnvelope → 追加 events.jsonl（即时持久化）→ 广播到 subscribers。
- `replay`：读 events.jsonl 全量 → 过滤 seq > after_seq → 返回 EventEnvelope 列表。
- `finish`：更新 meta.json status → 广播 None sentinel → 清理 subscribers。
- `cancel`：设 cancel_event → finish("cancelled")。
- `is_writing`：检查 `_locks[pid].locked()`。
- `recover_interrupted`：扫描各项目 turns 目录，meta.status=="running" → 改 "interrupted"。

```python
# web_host/turn_manager.py（完整实现骨架）
"""R4 回合生命周期管理器。"""
import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fieldwork_agent.web_host.events import EventEnvelope

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})


@dataclass
class TurnHandle:
    turn_id: str
    pid: str
    status: str = "running"
    idempotency_key: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    seq_counter: int = 0


class TurnManager:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._turns: dict[str, TurnHandle] = {}
        self._idempotency: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def _key(self, pid: str, tid: str) -> str:
        return f"{pid}:{tid}"

    def _turn_dir(self, pid: str, tid: str) -> Path:
        return self.root / pid / "turns" / tid

    def create_turn(self, pid: str, idempotency_key: str | None = None) -> TurnHandle:
        if idempotency_key:
            ik = f"{pid}:{idempotency_key}"
            if ik in self._idempotency:
                return self._turns[self._key(pid, self._idempotency[ik])]
        tid = uuid.uuid4().hex[:16]
        handle = TurnHandle(turn_id=tid, pid=pid, idempotency_key=idempotency_key)
        self._turns[self._key(pid, tid)] = handle
        if idempotency_key:
            self._idempotency[f"{pid}:{idempotency_key}"] = tid
        # 持久化 meta
        d = self._turn_dir(pid, tid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps(
            {"turn_id": tid, "status": "running", "created_at": handle.created_at,
             "idempotency_key": idempotency_key}, ensure_ascii=False), encoding="utf-8")
        (d / "events.jsonl").touch(exist_ok=True)
        return handle

    def get_turn(self, pid: str, tid: str) -> TurnHandle | None:
        return self._turns.get(self._key(pid, tid))

    def append_event(self, pid: str, tid: str, event: str, data: dict) -> EventEnvelope:
        handle = self._turns[self._key(pid, tid)]
        handle.seq_counter += 1
        env = EventEnvelope(turn_id=tid, seq=handle.seq_counter, event=event, data=data)
        # 即时持久化
        with open(self._turn_dir(pid, tid) / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(env.model_dump_json() + "\n")
        # 广播
        for q in self._subscribers.get(self._key(pid, tid), []):
            q.put_nowait(env)
        return env

    def replay(self, pid: str, tid: str, after_seq: int) -> list[EventEnvelope]:
        path = self._turn_dir(pid, tid) / "events.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            env = EventEnvelope.model_validate_json(line)
            if env.seq > after_seq:
                out.append(env)
        return out

    def finish(self, pid: str, tid: str, status: str) -> None:
        handle = self._turns.get(self._key(pid, tid))
        if handle is None:
            return
        handle.status = status
        meta_path = self._turn_dir(pid, tid) / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["status"] = status
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        # 广播终止信号
        for q in self._subscribers.get(self._key(pid, tid), []):
            q.put_nowait(None)

    def cancel(self, pid: str, tid: str) -> bool:
        handle = self.get_turn(pid, tid)
        if handle is None or handle.status in TERMINAL_STATUSES:
            return False
        handle.cancel_event.set()
        self.finish(pid, tid, "cancelled")
        return True

    def is_writing(self, pid: str) -> bool:
        lock = self._locks.get(pid)
        return lock is not None and lock.locked()

    def acquire_write_lock(self, pid: str):
        if pid not in self._locks:
            self._locks[pid] = asyncio.Lock()
        return self._locks[pid]

    def subscribe(self, pid: str, tid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(self._key(pid, tid), []).append(q)
        return q

    def unsubscribe(self, pid: str, tid: str, q: asyncio.Queue) -> None:
        subs = self._subscribers.get(self._key(pid, tid), [])
        if q in subs:
            subs.remove(q)

    def recover_interrupted(self, pid_list: list[str]) -> None:
        for pid in pid_list:
            turns_dir = self.root / pid / "turns"
            if not turns_dir.exists():
                continue
            for td in turns_dir.iterdir():
                meta_path = td / "meta.json"
                if not meta_path.exists():
                    continue
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("status") == "running":
                    meta["status"] = "interrupted"
                    meta_path.write_text(json.dumps(meta, ensure_ascii=False),
                                         encoding="utf-8")
```

- [ ] **Step 4: 确认通过** — `pytest tests/test_web_turn_manager.py -v`
- [ ] **Step 5: Commit** — `& "C:\Program Files\Git\cmd\git.exe" add web_host/turn_manager.py tests/test_web_turn_manager.py; & "C:\Program Files\Git\cmd\git.exe" commit -m "feat(web): R4回合管理器(状态机/幂等/写锁/事件持久化/回放)"`

---

### Task 4: REST API — 项目 CRUD

**Files:**
- Modify: `web_host/api/projects.py`
- Test: `tests/test_web_projects_api.py`（追加）

**Interfaces:**
- Consumes: `ProjectRepository`、`deps.get_repo`
- Produces: `POST /projects`(201) / `GET /projects` / `GET /projects/{id}`

- [ ] **Step 1: 写失败测试（追加）**

```python
# tests/test_web_projects_api.py 追加
async def test_create_project(client):
    r = await client.post("/projects", json={"project_id": "p_test",
                                             "discipline": "人类学", "topic": "宗族"})
    assert r.status_code == 201
    assert r.json()["project_id"] == "p_test"
    assert r.json()["phase"] == "research_positioning"


async def test_create_project_auto_id(client):
    r = await client.post("/projects", json={})
    assert r.status_code == 201
    assert r.json()["project_id"].startswith("field_")


async def test_list_projects(client):
    await client.post("/projects", json={"project_id": "p1"})
    await client.post("/projects", json={"project_id": "p2"})
    r = await client.get("/projects")
    assert r.status_code == 200
    assert {"p1", "p2"} <= {p["project_id"] for p in r.json()}


async def test_get_project_snapshot(client):
    await client.post("/projects", json={"project_id": "p1", "topic": "白族"})
    r = await client.get("/projects/p1")
    assert r.status_code == 200
    snap = r.json()
    assert snap["phase"] == "research_positioning"
    assert snap["research_context"]["topic"] == "白族"
    assert "field_sites" in snap and "informants" in snap


async def test_get_project_404(client):
    r = await client.get("/projects/ghost")
    assert r.status_code == 404
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_web_projects_api.py -v`

- [ ] **Step 3: 实现** — `web_host/api/projects.py`：

```python
# web_host/api/projects.py
"""项目 CRUD 路由（spec §3.2）。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from fieldwork_agent.web_host.deps import get_repo

router = APIRouter(tags=["projects"])


class CreateProjectRequest(BaseModel):
    project_id: str | None = None
    discipline: str = ""
    topic: str = ""
    purpose: str = ""


@router.post("/projects", status_code=201)
async def create_project(body: CreateProjectRequest, repo=Depends(get_repo)):
    pid = body.project_id or f"field_{datetime.now():%Y%m%d_%H%M%S}"
    repo.create(pid)
    patch = {k: v for k, v in {"discipline": body.discipline,
                                "topic": body.topic, "purpose": body.purpose}.items() if v}
    if patch:
        repo.update_research_context(pid, patch)
    return {"project_id": pid, "phase": repo.get_phase(pid)}


@router.get("/projects")
async def list_projects(repo=Depends(get_repo)):
    results = []
    for summary in repo.list_projects():
        pid = summary["project_id"]
        ctx = repo.get_research_context(pid)
        results.append({**summary, "research_context": ctx,
                        "counts": {"sites": len(repo.list_field_sites(pid)),
                                   "informants": len(repo.list_informants(pid)),
                                   "proposals": len(repo.list_proposals(pid))}})
    return results


@router.get("/projects/{project_id}")
async def get_project(project_id: str, repo=Depends(get_repo)):
    try:
        state = repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}")
    return {"project_id": project_id, "phase": state["phase"],
            "research_context": repo.get_research_context(project_id),
            "field_sites": repo.list_field_sites(project_id),
            "informants": repo.list_informants(project_id),
            "proposals": [{"id": p["id"], "kind": p.get("kind"),
                           "version": p.get("version"), "created_at": p.get("created_at")}
                          for p in repo.list_proposals(project_id)],
            "phase_history": state.get("phase_history", []),
            "updated_at": state.get("updated_at")}
```

- [ ] **Step 4: 确认通过** — `pytest tests/test_web_projects_api.py -v`
- [ ] **Step 5: Commit** — `& "C:\Program Files\Git\cmd\git.exe" add web_host/api/projects.py tests/test_web_projects_api.py; & "C:\Program Files\Git\cmd\git.exe" commit -m "feat(web): 项目CRUD REST端点"`

---

### Task 5: LLM 流式扩展（kernel/llm.py 增 chat_stream）

**Files:**
- Modify: `kernel/llm.py`（追加 `chat_stream` 方法）
- Test: `tests/test_llm_config.py`（追加）

**Interfaces:**
- Consumes: DashScope `Generation.call(stream=True, incremental_output=True)`
- Produces: `LLMClient.chat_stream(messages, *, slot, tools, temperature, max_tokens) -> Generator[dict]` — 逐 token yield `{"delta": str}`，最终 yield `{"content", "tool_calls", "finish_reason", "usage"}`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_llm_config.py 追加
from unittest.mock import MagicMock, patch


def test_chat_stream_yields_deltas_then_final():
    from fieldwork_agent.kernel.llm import LLMClient

    client = LLMClient.__new__(LLMClient)
    client.api_key = "fake"
    client.models = {"interview": "q", "proposal": "q", "extract": "q"}

    chunks = [
        MagicMock(status_code=200, output=MagicMock(choices=[
            MagicMock(message=MagicMock(content="你", tool_calls=None),
                      finish_reason="null")]), usage=None),
        MagicMock(status_code=200, output=MagicMock(choices=[
            MagicMock(message=MagicMock(content="好", tool_calls=None),
                      finish_reason="null")]), usage=None),
        MagicMock(status_code=200, output=MagicMock(choices=[
            MagicMock(message=MagicMock(content="", tool_calls=None),
                      finish_reason="stop")]), usage={"total_tokens": 10}),
    ]
    with patch("fieldwork_agent.kernel.llm.Generation") as MockGen:
        MockGen.call.return_value = iter(chunks)
        results = list(client.chat_stream([{"role": "user", "content": "hi"}],
                                          slot="interview"))
    deltas = [r for r in results if "delta" in r]
    assert "".join(d["delta"] for d in deltas) == "你好"
    final = results[-1]
    assert final["content"] == "你好" and final["finish_reason"] == "stop"
    assert final["usage"] == {"total_tokens": 10}
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_llm_config.py::test_chat_stream_yields_deltas_then_final -v`

- [ ] **Step 3: 实现** — 在 `kernel/llm.py` 的 `LLMClient` 类追加 `chat_stream` 方法：

```python
def chat_stream(self, messages: list[dict], *, slot: str = "interview",
                tools: list[dict] | None = None, temperature: float = 0.7,
                max_tokens: int = 4096):
    """流式对话：逐 token yield {"delta": str}，最终 yield 四键终止帧。"""
    call_kwargs: dict[str, Any] = {
        "model": self.models[slot], "messages": messages,
        "api_key": self.api_key, "result_format": "message",
        "temperature": temperature, "max_tokens": max_tokens,
        "stream": True, "incremental_output": True,
    }
    if tools:
        call_kwargs["tools"] = tools
    accumulated = ""
    final_tool_calls = None
    finish_reason = ""
    usage: dict = {}
    for response in Generation.call(**call_kwargs):
        if response.status_code != 200:
            yield {"content": "", "tool_calls": None, "finish_reason": "",
                   "usage": {}, "error": str(response.message)}
            return
        choice = response.output.choices[0]
        message = choice.message
        delta = _field(message, "content") or ""
        if delta:
            accumulated += delta
            yield {"delta": delta}
        tc = _parse_tool_calls(_field(message, "tool_calls"))
        if tc:
            final_tool_calls = tc
        fr = _field(choice, "finish_reason") or ""
        if fr and fr != "null":
            finish_reason = fr
        if response.usage:
            usage = response.usage
    yield {"content": accumulated, "tool_calls": final_tool_calls,
           "finish_reason": finish_reason, "usage": usage}
```

 [ ] **Step 4: 确认通过** — `pytest tests/test_llm_config.py -v`；全量 `pytest -q` 无回归
- [ ] **Step 5: Commit** — `& "C:\Program Files\Git\cmd\git.exe" add kernel/llm.py tests/test_llm_config.py; & "C:\Program Files\Git\cmd\git.exe" commit -m "feat(kernel): LLMClient.chat_stream流式输出(增量delta+终止帧)"`

---

### Task 6: Agent Loop — 上下文组装 + 流式循环 + 并行工具

**Files:**
- Create: `web_host/agent/__init__.py`, `web_host/agent/context.py`, `web_host/agent/dispatch.py`, `web_host/agent/loop.py`
- Test: `tests/test_web_agent_loop.py`

**Interfaces:**
- Consumes: `kernel.guidance.phases.phase_prompt`、`kernel.registry.build_tool_schemas/capabilities`、`kernel.models.*`、`runtime.loop._tool_content/_coerce_args/_assistant_message`（复用摘要逻辑）、`TurnManager.append_event`、`LLMClient.chat_stream`、`ProjectRepository`
- Produces:
  - `context.build_system_prompt(phase: str, snapshot: str) -> str`
  - `context.build_snapshot(repo, pid) -> str`
  - `context.build_messages(repo, pid, user_text, phase, history_limit=30) -> list[dict]`
  - `dispatch.build_dispatch_fn(flyai_client, llm) -> Callable[[str, dict], Envelope]`（同步；web loop 用 `asyncio.to_thread` 包装）
  - `loop.run_turn(repo, pid, turn_id, user_text, tm, llm, flyai_client, *, max_iters=8, max_tool_calls=20, max_wall_seconds=60) -> None`（async）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web_agent_loop.py
import asyncio

import pytest

from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.turn_manager import TurnManager


class FakeLLM:
    """脚本化流式 LLM：chat_stream 按脚本 yield。"""
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat_stream(self, messages, **kw):
        self.calls.append((list(messages), kw))
        return iter(self.script.pop(0))


class FakeFlyAI:
    def __getattr__(self, name):
        def _stub(**kw):
            return Envelope(ok=True, data=ItemPage(
                items=[SearchItem(name=f"{name}-结果", price=100.0)],
                total=1, cheapest=100.0, dearest=100.0))
        return _stub


@pytest.fixture
def env(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    tm = TurnManager(root=tmp_path / "projects")
    return repo, tm


def test_simple_reply_emits_delta_and_turn_done(env):
    repo, tm = env
    llm = FakeLLM([[
        {"delta": "你好"}, {"delta": "世界"},
        {"content": "你好世界", "tool_calls": None, "finish_reason": "stop",
         "usage": {"total_tokens": 5}},
    ]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "message_delta" in types and types[-1] == "turn_done"
    deltas = [e.data["text"] for e in events if e.event == "message_delta"]
    assert "".join(deltas) == "你好世界"


def test_tool_call_emits_started_completed(env):
    repo, tm = env
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "search_hotels",
                                          "arguments": {"dest_name": "大理"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "已找到"},
         {"content": "已找到", "tool_calls": None, "finish_reason": "stop",
          "usage": {"total_tokens": 20}}],
    ])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "搜酒店", tm, llm, FakeFlyAI()))
    events = tm.replay("p1", turn.turn_id, after_seq=0)
    types = [e.event for e in events]
    assert "tool_started" in types and "tool_completed" in types
    started = next(e for e in events if e.event == "tool_started")
    assert started.data["call_id"] == "c1" and started.data["tool"] == "search_hotels"
    completed = next(e for e in events if e.event == "tool_completed")
    assert completed.data["ok"] is True


def test_user_message_persisted_immediately(env):
    """R4：用户消息即时持久化，不等轮末。"""
    repo, tm = env
    llm = FakeLLM([[{"delta": "ok"}, {"content": "ok", "tool_calls": None,
                    "finish_reason": "stop", "usage": {}}]])
    turn = tm.create_turn("p1")
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "测试消息", tm, llm, FakeFlyAI()))
    msgs = repo.get_messages("p1")
    assert any(m["role"] == "user" and m["content"] == "测试消息" for m in msgs)
    assert any(m["role"] == "assistant" and m["content"] == "ok" for m in msgs)


def test_cancel_stops_loop(env):
    """取消后 loop 不再发新事件，终态为 cancelled。"""
    repo, tm = env
    # 模拟一个会发 tool_calls 的 LLM（多轮），但我们在第一轮后取消
    llm = FakeLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "search_hotels",
                                          "arguments": {"dest_name": "X"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "不应到达"},
         {"content": "不应到达", "tool_calls": None, "finish_reason": "stop", "usage": {}}],
    ])
    turn = tm.create_turn("p1")
    tm.cancel("p1", turn.turn_id)  # 预取消
    from fieldwork_agent.web_host.agent.loop import run_turn
    asyncio.run(run_turn(repo, "p1", turn.turn_id, "hi", tm, llm, FakeFlyAI()))
    assert tm.get_turn("p1", turn.turn_id).status == "cancelled"
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_web_agent_loop.py -v`

- [ ] **Step 3: 实现**

`web_host/agent/context.py`：
```python
"""system prompt + 项目快照 + 历史消息组装（spec §3.3 步骤 1）。"""
from fieldwork_agent.kernel.guidance import phases
from fieldwork_agent.kernel.repository.protocol import ProjectRepository

_TOOL_GUIDANCE = (
    "工具使用指引：需要真实交通/住宿/景点/文化活动数据时，调用飞猪搜索工具"
    "（search_flights/search_trains/search_hotels/search_pois/search_keyword/search_ai 等）"
    "或聚合能力（profile_field_site/compose_itinerary）。"
    "文档能力（compose_proposal/compose_interview_guide/suggest_informants）用于产出方案。"
    "不要臆造数据；工具不可用时如实说明并给出可执行的下一步。"
)


def build_snapshot(repo: ProjectRepository, pid: str) -> str:
    state = repo.get_state(pid)
    ctx = state.get("research_context") or {}
    filled = [f"{k}={v}" for k, v in ctx.items() if k != "extras" and str(v).strip()]
    extras = ctx.get("extras") or {}
    filled += [f"{k}={v}" for k, v in extras.items()]
    n_sites = len(state.get("field_sites") or [])
    n_inf = len(state.get("informants") or [])
    n_prop = len(state.get("proposals") or [])
    return (f"当前项目快照：已采集[{'；'.join(filled) if filled else '（空）'}]；"
            f"候选田野点 {n_sites} 个、访谈对象 {n_inf} 位、方案草稿 {n_prop} 份。")


def build_system_prompt(phase: str, snapshot: str) -> str:
    return "\n\n".join([phases.phase_prompt(phase), _TOOL_GUIDANCE, snapshot])


def build_messages(repo: ProjectRepository, pid: str, user_text: str,
                   phase: str, history_limit: int = 30) -> list[dict]:
    snapshot = build_snapshot(repo, pid)
    system = build_system_prompt(phase, snapshot)
    history = repo.get_messages(pid, limit=history_limit)
    msgs = [{"role": "system", "content": system}]
    msgs += [{"role": m["role"], "content": m["content"]}
             for m in history if m.get("role") in ("user", "assistant")]
    msgs.append({"role": "user", "content": user_text})
    return msgs
```

`web_host/agent/dispatch.py`：
```python
"""能力分派：复用 kernel registry，同步 dispatch（loop 用 to_thread 包装）。"""
import inspect
from collections.abc import Callable
from typing import Any

from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.kernel.registry import capabilities
from pydantic import ValidationError


def build_dispatch_fn(flyai_client: Any, llm: Any) -> Callable[[str, dict], Envelope]:
    metas = capabilities()

    def dispatch(name: str, args: dict) -> Envelope:
        meta = metas.get(name)
        if meta is None:
            return Envelope(ok=False, error=ToolError(
                code="unknown_tool", message=f"未知工具: {name}"))
        try:
            validated = meta.args_model.model_validate(args).model_dump()
        except ValidationError as exc:
            msg = exc.errors()[0].get("msg", "") if exc.errors() else str(exc)
            return Envelope(ok=False, error=ToolError(
                code="invalid_arguments", message=f"参数校验失败：{msg}"))
        params = inspect.signature(meta.fn).parameters
        kwargs = dict(validated)
        if "client" in params:
            kwargs["client"] = flyai_client
        if "llm" in params:
            kwargs["llm"] = llm
        try:
            result = meta.fn(**kwargs)
        except Exception as exc:
            return Envelope(ok=False, error=ToolError(
                code="internal_error", message=str(exc)))
        return result if isinstance(result, Envelope) else Envelope(ok=True, data=result)

    return dispatch
```

`web_host/agent/loop.py`（async 回合循环核心）：
```python
"""async 工具回合循环（spec §3.3）：流式 LLM + 并行工具 + 事件发射。

与 runtime/loop.py（同步 CLI 用）共享摘要逻辑（_tool_content），但本模块：
- 全 async；LLM 流式 → 逐 token 发 message_delta；
- 工具并行 → asyncio.gather + to_thread；
- 事件即时持久化 → TurnManager.append_event（R4）；
- 取消检查 → 每轮迭代前检 cancel_event。
"""
import asyncio
import time
from typing import Any

from fieldwork_agent.kernel.registry import build_tool_schemas
from fieldwork_agent.runtime.loop import _assistant_message, _coerce_args, _tool_content
from fieldwork_agent.web_host.agent.context import build_messages
from fieldwork_agent.web_host.agent.dispatch import build_dispatch_fn
from fieldwork_agent.web_host.turn_manager import TurnManager


async def run_turn(repo, pid: str, turn_id: str, user_text: str,
                   tm: TurnManager, llm: Any, flyai_client: Any, *,
                   max_iters: int = 8, max_tool_calls: int = 20,
                   max_wall_seconds: float = 60.0) -> None:
    dispatch = build_dispatch_fn(flyai_client, llm)
    tools = build_tool_schemas()
    phase = repo.get_phase(pid)

    # R4：用户消息即时持久化
    repo.append_message(pid, "user", user_text)
    messages = build_messages(repo, pid, user_text, phase)

    iterations = 0
    total_calls = 0
    start = time.monotonic()
    accumulated_reply = ""
    usage_total: dict = {}
    call_name_map: dict[str, str] = {}  # call_id → tool name（落盘用）

    try:
        while True:
            handle = tm.get_turn(pid, turn_id)
            if handle and handle.cancel_event.is_set():
                break
            if iterations >= max_iters or total_calls >= max_tool_calls:
                break
            if time.monotonic() - start > max_wall_seconds:
                break

            # 流式 LLM
            stream = llm.chat_stream(messages, tools=tools, slot="interview")
            iterations += 1
            final_resp = None
            async for chunk in _async_iter(stream):
                if "delta" in chunk and chunk["delta"]:
                    tm.append_event(pid, turn_id, "message_delta",
                                    {"text": chunk["delta"]})
                elif "content" in chunk:
                    final_resp = chunk

            if final_resp is None:
                break
            accumulated_reply = final_resp.get("content", "") or accumulated_reply
            usage_total = final_resp.get("usage", {}) or usage_total
            tool_calls = final_resp.get("tool_calls")
            if not tool_calls:
                break

            # 并行工具执行
            messages.append(_assistant_message(final_resp))
            coros = []
            for call in tool_calls:
                name = call.get("name", "")
                call_id = call.get("id", "")
                args = _coerce_args(call.get("arguments"))
                call_name_map[call_id] = name
                tm.append_event(pid, turn_id, "tool_started",
                                {"call_id": call_id, "tool": name,
                                 "args": args, "purpose": ""})
                coros.append(_exec_tool(dispatch, name, args, call_id))

            results = await asyncio.gather(*coros)
            total_calls += len(results)

            for call_id, name, env in results:
                data = env.data
                if hasattr(data, "model_dump"):
                    data = data.model_dump(mode="json")
                tm.append_event(pid, turn_id, "tool_completed",
                                {"call_id": call_id, "ok": env.ok,
                                 "data": data, "degraded": env.degraded})
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "content": _tool_content(name, env)})

    except Exception as exc:
        tm.append_event(pid, turn_id, "error",
                        {"code": "internal_error", "message": str(exc), "hint": ""})
        tm.finish(pid, turn_id, "failed")
        return

    # 轮末：助手消息持久化 + 工具结果落盘 + 阶段推进
    if accumulated_reply:
        repo.append_message(pid, "assistant", accumulated_reply,
                            {"tool_calls": total_calls})
    _persist_tool_results(repo, pid, tm, turn_id, call_name_map)
    transition = _check_and_advance(repo, pid)
    if transition:
        tm.append_event(pid, turn_id, "phase_transition", transition)

    # 终态
    handle = tm.get_turn(pid, turn_id)
    if handle and handle.cancel_event.is_set():
        tm.finish(pid, turn_id, "cancelled")
    else:
        tm.append_event(pid, turn_id, "turn_done",
                        {"reply_id": turn_id, "usage": usage_total})
        tm.finish(pid, turn_id, "completed")


async def _async_iter(sync_gen):
    """同步 generator → async iterator（LLM stream 是同步的）。"""
    it = iter(sync_gen)
    while True:
        try:
            yield await asyncio.to_thread(next, it)
        except StopIteration:
            break


async def _exec_tool(dispatch, name, args, call_id):
    env = await asyncio.to_thread(dispatch, name, args)
    return (call_id, name, env)


def _persist_tool_results(repo, pid, tm, turn_id, call_name_map):
    """轮末把成功工具结果落盘（搜索→cache_search；画像→save_field_site）。"""
    _SEARCH = frozenset({"search_flights", "search_trains", "search_hotels",
                         "search_marriott_hotels", "search_marriott_packages",
                         "search_pois", "search_keyword", "search_ai"})
    events = tm.replay(pid, turn_id, after_seq=0)
    for ev in events:
        if ev.event != "tool_completed" or not ev.data.get("ok"):
            continue
        call_id = ev.data.get("call_id", "")
        name = call_name_map.get(call_id, "")
        data = ev.data.get("data")
        if not name or data is None:
            continue
        data_dict = data if isinstance(data, dict) else (
            data.model_dump(mode="json") if hasattr(data, "model_dump") else {"value": data})
        if name in _SEARCH:
            repo.cache_search(pid, f"{name}: {call_id}", name, data_dict)
        elif name == "profile_field_site":
            repo.save_field_site(pid, {"name": data_dict.get("location", ""),
                                       "confirmed": False, "profile": data_dict})
        elif name == "compose_itinerary":
            dests = ",".join(data_dict.get("destinations", []) or [])
            repo.cache_search(pid, f"itinerary::{dests}", "compose_itinerary", data_dict)


def _check_and_advance(repo, pid) -> dict | None:
    """确定性阶段推进（spec §3.3：绝不由 LLM 宣布）。"""
    from fieldwork_agent.kernel.guidance import phases
    from fieldwork_agent.kernel.models import (FieldSiteRecord, InformantRecord,
                                                ResearchContext)
    state = repo.get_state(pid)
    ctx = ResearchContext.model_validate(state.get("research_context") or {})
    sites = [FieldSiteRecord.model_validate(s) for s in state.get("field_sites") or []]
    informants = [InformantRecord.model_validate(i) for i in state.get("informants") or []]
    has_itinerary = any(c.get("tool") == "compose_itinerary"
                        for c in repo.list_cached_searches(pid))
    result = phases.check_advance(
        state["phase"], ctx, sites, informants,
        has_itinerary, bool(state.get("proposals")),
        bool(state.get("proposal_confirmed")))
    if result.can_advance and result.to_phase:
        old = state["phase"]
        repo.set_phase(pid, result.to_phase)
        return {"from": old, "to": result.to_phase}
    return None
```

- [ ] **Step 4: 确认通过** — `pytest tests/test_web_agent_loop.py -v`
- [ ] **Step 5: Commit** — `& "C:\Program Files\Git\cmd\git.exe" add web_host/agent tests/test_web_agent_loop.py; & "C:\Program Files\Git\cmd\git.exe" commit -m "feat(web): async agent loop(流式LLM+并行工具+事件发射+阶段推进)"`

---

### Task 7: 回合 API + SSE 流 + 取消

**Files:**
- Modify: `web_host/api/turns.py`
- Test: `tests/test_web_turns_api.py`

**Interfaces:**
- Consumes: `TurnManager`、`agent.loop.run_turn`、`sse.*`、`deps.*`
- Produces:
  - `POST /projects/{id}/messages` body `{text, idempotency_key?}` → 201 `{turn_id, status}`
  - `GET /projects/{id}/turns/{tid}/events` → SSE（Last-Event-ID 回放 + 实时 + 心跳）
  - `POST /projects/{id}/turns/{tid}/cancel` → `{turn_id, status}`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web_turns_api.py
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from fieldwork_agent.web_host.app import create_app


class FakeLLM:
    def chat_stream(self, messages, **kw):
        return iter([{"delta": "收到"}, {"content": "收到", "tool_calls": None,
                     "finish_reason": "stop", "usage": {}}])
    def chat(self, messages, **kw):
        return {"content": "收到", "tool_calls": None, "finish_reason": "stop", "usage": {}}


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    application = create_app(config=None, repo=repo)
    return application


@pytest.fixture
async def client(app):
    # 注入 FakeLLM（lifespan 已跑完，直接替换 state）
    app.state.llm = FakeLLM()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_create_turn(client):
    r = await client.post("/projects/p1/messages",
                          json={"text": "你好", "idempotency_key": "k1"})
    assert r.status_code == 201
    assert "turn_id" in r.json() and r.json()["status"] == "running"


async def test_idempotent_creation(client):
    r1 = await client.post("/projects/p1/messages",
                           json={"text": "hi", "idempotency_key": "same"})
    r2 = await client.post("/projects/p1/messages",
                           json={"text": "hi", "idempotency_key": "same"})
    assert r1.json()["turn_id"] == r2.json()["turn_id"]


async def test_sse_stream_events(client):
    r = await client.post("/projects/p1/messages", json={"text": "hello"})
    tid = r.json()["turn_id"]
    await asyncio.sleep(0.5)  # 等后台完成
    async with client.stream("GET", f"/projects/p1/turns/{tid}/events") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk
            if "turn_done" in body:
                break
    assert "message_delta" in body and "turn_done" in body


async def test_cancel_turn(client):
    r = await client.post("/projects/p1/messages", json={"text": "x"})
    tid = r.json()["turn_id"]
    cr = await client.post(f"/projects/p1/turns/{tid}/cancel")
    assert cr.status_code == 200
    assert cr.json()["status"] in ("cancelled", "completed")


async def test_unknown_project_404(client):
    r = await client.post("/projects/ghost/messages", json={"text": "hi"})
    assert r.status_code == 404


async def test_unknown_turn_404(client):
    r = await client.get("/projects/p1/turns/nonexist/events")
    assert r.status_code == 404
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_web_turns_api.py -v`

- [ ] **Step 3: 实现** — `web_host/api/turns.py`：

```python
# web_host/api/turns.py
"""回合路由（R4）：创建/SSE 订阅/取消。"""
import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from fieldwork_agent.web_host.deps import get_repo, get_turn_manager, get_llm
from fieldwork_agent.web_host.sse import (HEARTBEAT_INTERVAL, encode_event,
                                           encode_heartbeat, parse_last_event_id,
                                           sse_response)

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
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}")
    if tm.is_writing(project_id):
        raise HTTPException(409, "该项目有正在执行的回合")
    turn = tm.create_turn(project_id, idempotency_key=body.idempotency_key)
    background.add_task(_execute_turn, request.app, project_id,
                        turn.turn_id, body.text, llm)
    return {"turn_id": turn.turn_id, "status": turn.status}


async def _execute_turn(app, pid, tid, text, llm):
    from fieldwork_agent.web_host.agent.loop import run_turn
    from fieldwork_agent.web_host.deps import get_flyai_raw, get_repo_raw, get_tm_raw
    repo = get_repo_raw(app)
    tm = get_tm_raw(app)
    flyai = get_flyai_raw(app)
    async with tm.acquire_write_lock(pid):
        await run_turn(repo, pid, tid, text, tm, llm, flyai)


@router.get("/projects/{project_id}/turns/{turn_id}/events")
async def stream_events(project_id: str, turn_id: str, request: Request):
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
        from fieldwork_agent.web_host.turn_manager import TERMINAL_STATUSES
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
    tm = get_turn_manager(request)
    handle = tm.get_turn(project_id, turn_id)
    if handle is None:
        raise HTTPException(404, "回合不存在")
    tm.cancel(project_id, turn_id)
    return {"turn_id": turn_id, "status": handle.status}
```

- [ ] **Step 4: 确认通过** — `pytest tests/test_web_turns_api.py -v`
- [ ] **Step 5: Commit** — `& "C:\Program Files\Git\cmd\git.exe" add web_host/api/turns.py tests/test_web_turns_api.py; & "C:\Program Files\Git\cmd\git.exe" commit -m "feat(web): 回合API(创建/SSE流/取消)+R4写锁+后台执行"`

---

### Task 8: 提案导出 API

**Files:**
- Modify: `web_host/api/exports.py`
- Test: `tests/test_web_exports_api.py`

**Interfaces:**
- Consumes: `ProjectRepository.get_latest_proposal/list_proposals`
- Produces:
  - `GET /projects/{id}/proposal` → `{id, markdown, kind, version, sections}` 或 404
  - `GET /projects/{id}/proposal/download` → `text/markdown` + Content-Disposition attachment
  - `GET /projects/{id}/proposals` → `[{id, kind, version, created_at}]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web_exports_api.py
import pytest
from httpx import ASGITransport, AsyncClient

from fieldwork_agent.web_host.app import create_app


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    repo.save_proposal("p1", "# 标题\n\n## 一、背景\n\n内容",
                       kind="proposal", version="draft")
    return create_app(config=None, repo=repo)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_get_proposal(client):
    r = await client.get("/projects/p1/proposal")
    assert r.status_code == 200
    body = r.json()
    assert "# 标题" in body["markdown"]
    assert body["kind"] == "proposal" and body["version"] == "draft"


async def test_download_proposal(client):
    r = await client.get("/projects/p1/proposal/download")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "# 标题" in r.text


async def test_list_proposals(client):
    r = await client.get("/projects/p1/proposals")
    assert r.status_code == 200
    assert len(r.json()) >= 1


async def test_proposal_404(client):
    r = await client.get("/projects/ghost/proposal")
    assert r.status_code == 404


async def test_no_proposal_404(client, tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    repo = JsonRepository(projects_dir=tmp_path / "p2")
    repo.create("empty")
    app2 = create_app(config=None, repo=repo)
    transport = ASGITransport(app=app2)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/projects/empty/proposal")
    assert r.status_code == 404
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_web_exports_api.py -v`

- [ ] **Step 3: 实现** — `web_host/api/exports.py`：

```python
# web_host/api/exports.py
"""提案导出路由（spec §3.2 exports）。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from fieldwork_agent.web_host.deps import get_repo

router = APIRouter(tags=["exports"])


@router.get("/projects/{project_id}/proposal")
async def get_proposal(project_id: str, repo=Depends(get_repo)):
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}")
    doc = repo.get_latest_proposal(project_id)
    if doc is None:
        raise HTTPException(404, "尚无提案文档")
    content = doc.get("content", "")
    sections = [ln.lstrip("# ").strip() for ln in content.splitlines()
                if ln.startswith("## ")]
    return {"id": doc.get("id"), "markdown": content,
            "kind": doc.get("kind", "proposal"),
            "version": doc.get("version", "draft"), "sections": sections}


@router.get("/projects/{project_id}/proposal/download")
async def download_proposal(project_id: str, repo=Depends(get_repo)):
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}")
    doc = repo.get_latest_proposal(project_id)
    if doc is None:
        raise HTTPException(404, "尚无提案文档")
    content = doc.get("content", "")
    filename = doc.get("filename", f"{project_id}_proposal.md")
    return PlainTextResponse(
        content, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/projects/{project_id}/proposals")
async def list_proposals(project_id: str, repo=Depends(get_repo)):
    try:
        repo.get_state(project_id)
    except KeyError:
        raise HTTPException(404, f"项目不存在: {project_id}")
    return [{"id": p["id"], "kind": p.get("kind"), "version": p.get("version"),
             "created_at": p.get("created_at")} for p in repo.list_proposals(project_id)]
```

- [ ] **Step 4: 确认通过** — `pytest tests/test_web_exports_api.py -v`
- [ ] **Step 5: Commit** — `& "C:\Program Files\Git\cmd\git.exe" add web_host/api/exports.py tests/test_web_exports_api.py; & "C:\Program Files\Git\cmd\git.exe" commit -m "feat(web): 提案导出API(查看/下载.md/版本列表)"`

---

### Task 9: L3③ Web Loop 契约测试 + R6 边界场景

**Files:**
- Create: `tests/test_web_contract.py`
- Modify: `tests/conftest.py`（追加 web 宿主共用 fixture）

**Interfaces:**
- Consumes: 全部 Task 1-8 产物
- Produces: L3③ 契约测试套件（fake LLM 脚本化 tool_calls 驱动完整回合，断言并行分组/迭代上限/SSE 事件配对/幂等/取消/回放）+ R6 边界场景覆盖

- [ ] **Step 1: 写契约测试**

```python
# tests/test_web_contract.py
"""L3③ Web loop 契约测试（spec §6）+ R6 业务边界场景。

全 mock：FakeLLM 脚本化 + FakeFlyAI + tmp_path repo，不联网、无 key。
"""
import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem, ToolError
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.app import create_app
from fieldwork_agent.web_host.turn_manager import TurnManager


class ScriptedLLM:
    """按脚本返回 chat_stream 帧序列。"""
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat_stream(self, messages, **kw):
        self.calls.append((list(messages), kw))
        return iter(self.script.pop(0))


class StubFlyAI:
    """8 搜索原语恒返回单条目成功包络。"""
    def __getattr__(self, name):
        def _stub(**kw):
            return Envelope(ok=True, data=ItemPage(
                items=[SearchItem(name=f"{name}-A", price=888.0, price_raw="¥888")],
                total=1, cheapest=888.0, dearest=888.0))
        return _stub


class DegradedFlyAI:
    """flyai 不可用降级。"""
    def __getattr__(self, name):
        def _stub(**kw):
            return Envelope(ok=False, error=ToolError(
                code="flyai_unavailable", message="CLI not found", hint="安装 flyai"))
        return _stub


@pytest.fixture
def web_env(tmp_path):
    repo = JsonRepository(projects_dir=tmp_path / "projects")
    repo.create("p1")
    app = create_app(config=None, repo=repo)
    app.state.flyai = StubFlyAI()
    tm = app.state.turn_manager
    return app, repo, tm


async def _full_turn(app, repo, tm, pid, text, llm):
    """辅助：创建 turn → 执行 → 等完成 → 返回事件列表。"""
    from fieldwork_agent.web_host.agent.loop import run_turn
    app.state.llm = llm
    turn = tm.create_turn(pid)
    await run_turn(repo, pid, turn.turn_id, text, tm, llm, app.state.flyai)
    return turn, tm.replay(pid, turn.turn_id, after_seq=0)


@pytest.mark.asyncio
async def test_full_turn_event_sequence(web_env):
    """完整回合：message_delta → tool_started → tool_completed → message_delta → turn_done。"""
    app, repo, tm = web_env
    llm = ScriptedLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "search_hotels",
                                          "arguments": {"dest_name": "大理"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "找到"}, {"delta": "酒店"},
         {"content": "找到酒店", "tool_calls": None, "finish_reason": "stop",
          "usage": {"total_tokens": 30}}],
    ])
    turn, events = await _full_turn(app, repo, tm, "p1", "搜大理住宿", llm)
    types = [e.event for e in events]
    assert types[0] == "tool_started"
    assert "tool_completed" in types
    assert types[-1] == "turn_done"
    assert "message_delta" in types
    # 序号单调递增
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


@pytest.mark.asyncio
async def test_iteration_cap_stops(web_env):
    """R5：超过 max_iters 后收敛，不无限循环。"""
    app, repo, tm = web_env
    tc_frame = [{"delta": ""},
                {"content": "", "tool_calls": [{"id": "x", "name": "search_ai",
                                                 "arguments": {"query": "q"}}],
                 "finish_reason": "tool_calls", "usage": {}}]
    final_frame = [{"delta": "总结"},
                   {"content": "总结", "tool_calls": None, "finish_reason": "stop",
                    "usage": {}}]
    llm = ScriptedLLM([tc_frame] * 10 + [final_frame])
    turn, events = await _full_turn(app, repo, tm, "p1", "无限搜索", llm)
    # 应在 8 次迭代内停止
    tool_starts = [e for e in events if e.event == "tool_started"]
    assert len(tool_starts) <= 8
    assert events[-1].event == "turn_done"


@pytest.mark.asyncio
async def test_flyai_degraded_envelope(web_env):
    """R6：flyai 不可用 → tool_completed.ok=false + error code 可见，回合不崩。"""
    app, repo, tm = web_env
    app.state.flyai = DegradedFlyAI()
    llm = ScriptedLLM([
        [{"delta": ""},
         {"content": "", "tool_calls": [{"id": "c1", "name": "search_flights",
                                          "arguments": {"origin": "北京",
                                                        "destination": "大理"}}],
          "finish_reason": "tool_calls", "usage": {}}],
        [{"delta": "搜索不可用"},
         {"content": "搜索不可用", "tool_calls": None, "finish_reason": "stop",
          "usage": {}}],
    ])
    turn, events = await _full_turn(app, repo, tm, "p1", "搜航班", llm)
    completed = next(e for e in events if e.event == "tool_completed")
    assert completed.data["ok"] is False
    assert events[-1].event == "turn_done"  # 回合正常收尾


@pytest.mark.asyncio
async def test_idempotent_turn_no_double_execution(web_env):
    """R4：同幂等键不重复执行工具。"""
    app, repo, tm = web_env
    llm = ScriptedLLM([[{"delta": "ok"}, {"content": "ok", "tool_calls": None,
                        "finish_reason": "stop", "usage": {}}]])
    app.state.llm = llm
    t1 = tm.create_turn("p1", idempotency_key="idem-1")
    t2 = tm.create_turn("p1", idempotency_key="idem-1")
    assert t1.turn_id == t2.turn_id
    from fieldwork_agent.web_host.agent.loop import run_turn
    await run_turn(repo, "p1", t1.turn_id, "hi", tm, llm, app.state.flyai)
    events = tm.replay("p1", t1.turn_id, after_seq=0)
    # 只有一份事件（不重复）
    assert sum(1 for e in events if e.event == "turn_done") == 1


@pytest.mark.asyncio
async def test_replay_after_disconnect(web_env):
    """R4：断线后按 Last-Event-ID 回放，事件不丢不重。"""
    app, repo, tm = web_env
    llm = ScriptedLLM([[{"delta": "a"}, {"delta": "b"}, {"delta": "c"},
                        {"content": "abc", "tool_calls": None,
                         "finish_reason": "stop", "usage": {}}]])
    turn, events = await _full_turn(app, repo, tm, "p1", "hi", llm)
    # 模拟断线在 seq=2 后重连
    replayed = tm.replay("p1", turn.turn_id, after_seq=2)
    assert all(e.seq > 2 for e in replayed)
    assert len(replayed) == len(events) - 2


@pytest.mark.asyncio
async def test_phase_transition_deterministic(web_env):
    """spec §3.3：阶段推进由 check_advance 确定性判定，非 LLM 宣布。"""
    app, repo, tm = web_env
    # 预置足够字段触发 A→B
    repo.update_research_context("p1", {
        "purpose": "开题", "discipline": "人类学", "topic": "宗族",
        "scale": "单人", "budget": "2万", "time_window": "三个月"})
    llm = ScriptedLLM([[{"delta": "好的"},
                        {"content": "好的", "tool_calls": None,
                         "finish_reason": "stop", "usage": {}}]])
    turn, events = await _full_turn(app, repo, tm, "p1", "开始", llm)
    transitions = [e for e in events if e.event == "phase_transition"]
    assert len(transitions) == 1
    assert transitions[0].data["from"] == "research_positioning"
    assert transitions[0].data["to"] == "field_site_profiles"
```

- [ ] **Step 2: 确认失败** — `pytest tests/test_web_contract.py -v`
- [ ] **Step 3: 修复实现使测试通过**（可能需要微调 loop/turn_manager 的边界处理）
- [ ] **Step 4: 确认通过** — `pytest tests/test_web_contract.py -v`；全量 `pytest -q && ruff check .`
- [ ] **Step 5: Commit** — `& "C:\Program Files\Git\cmd\git.exe" add tests/test_web_contract.py tests/conftest.py; & "C:\Program Files\Git\cmd\git.exe" commit -m "test(web): L3③全链路回合契约+R6边界场景"`

---

### Task 10: 集成收尾 — uvicorn 入口 + 文档 + CI

**Files:**
- Modify: `README.md`（Web 宿主启动段）
- Modify: `DEVELOPMENT.md`（§5 扩展点补 Web 宿主条目）
- Modify: `.github/workflows/`（CI 追加 web 测试）
- Modify: `pyproject.toml`（确认 packages 完整）

**Interfaces:**
- Consumes: 全部 Task 1-9
- Produces: `fieldwork-web` console script 可用；README 文档；CI 全绿

- [ ] **Step 1: 验证 console script**

```bash
pip install -e ".[dev,mcp]"
fieldwork-web  # 应启动 uvicorn 在 127.0.0.1:8300
# 另一终端：curl http://127.0.0.1:8300/health → {"status":"ok"}
# curl -X POST http://127.0.0.1:8300/projects -H "Content-Type: application/json" -d '{}'
```

- [ ] **Step 2: README 追加 Web 宿主段**

```markdown
## Web 宿主（FastAPI）

```bash
pip install -e ".[dev]"
fieldwork-web                    # 启动在 http://127.0.0.1:8300
```

开发期前端（Plan 4）通过 Vite proxy 转发 `/projects` 到 `:8300`。

端点概览：
- `POST /projects` — 创建项目
- `GET /projects/{id}` — 项目快照
- `POST /projects/{id}/messages` — 创建回合（返回 turn_id）
- `GET /projects/{id}/turns/{tid}/events` — SSE 事件流
- `POST /projects/{id}/turns/{tid}/cancel` — 取消回合
- `GET /projects/{id}/proposal` — 最新提案
```

- [ ] **Step 3: DEVELOPMENT.md §5 扩展点补 Web 宿主条目**

追加说明：
- 新增 SSE 事件类型：修改 `web_host/events.py` + `sse.py` 编码
- 新增 REST 端点：在 `web_host/api/` 对应 router 文件追加
- Agent loop 扩展：`web_host/agent/loop.py` 的 `run_turn`
- 回合协议（R4）：`web_host/turn_manager.py`

- [ ] **Step 4: CI 配置**

在 `.github/workflows/` 现有测试矩阵中确认 `pip install -e ".[dev,mcp]"` 包含 fastapi/uvicorn/httpx 依赖，`pytest -q` 覆盖 `tests/test_web_*.py`。

- [ ] **Step 5: 全量验证 + Commit**

```bash
pytest -q          # 全绿（含既有 kernel/mcp 测试无回归）
ruff check .       # 干净
fieldwork-web      # 可启动
```

`& "C:\Program Files\Git\cmd\git.exe" add -A; & "C:\Program Files\Git\cmd\git.exe" commit -m "feat(web): 集成收尾(uvicorn入口+文档+CI)"`

---

## 验收（整盘）

1. `pytest -q` 全绿（含既有 kernel/runtime/mcp_server 测试无回归）；`ruff check .` 干净；测试不依赖网络/flyai/DashScope key。
2. `fieldwork-web` 启动 uvicorn 在 `:8300`；`GET /health` → 200；`POST /projects` → 201；`GET /projects/{id}` → 完整快照。
3. 回合全链路：`POST /projects/{id}/messages` → 201 `{turn_id}`；`GET .../turns/{tid}/events` → SSE 流含 message_delta/tool_started/tool_completed/turn_done；序号单调递增。
4. R4 协议逐条可验：幂等键不重复执行；Last-Event-ID 回放不丢不重；取消 → cancelled 终态；写锁 → 同项目 409；进程重启 → interrupted。
5. 无 flyai CLI 环境：搜索工具返回 `tool_completed.ok=false` + `flyai_unavailable`，回合正常收尾不崩。
6. 无 DashScope key：LLM 不可用时回合返回 `error` 事件 + `failed` 终态（或降级文案，取决于实现选择）。
7. 阶段推进确定性：满足 check_advance 条件时自动发 `phase_transition`；不满足时不发（LLM 无法宣布推进）。
8. L3③ 契约测试覆盖：并行分组、8 迭代上限、SSE 事件配对、幂等、取消、回放。
9. Plan 4 前端可对接：全部端点形状与本计划「API 端点总览」表一致；SSE 事件 payload 与 spec §3.4 六事件字段对齐。
