# 审计第一批修复：部署边界封闭（F01/F02/F05/F15）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地《2026-10-16 项目开发现状审计》第一批四项修复：F01 封堵 Web 项目 ID 目录穿越、F02 增加应用访问令牌（后端校验 + 前端填写）、F05 把浏览器提交的 FlyAI Key 传入 flyai 子进程、F15 补齐部署脚本的 flyai CLI 依赖安装与不吞错的就绪检查。

**Architecture:** project_id / turn_id 校验规则从 `mcp_server/state_tools.py` 下沉到 `kernel/repository/guard.py` 作为单一实现来源（含语义修正：`..` 由子串匹配改为整值匹配），MCP 与 Web 各自薄壳包装——MCP 折 `invalid_args` 错误包络并回显输入，Web 折 `HTTPException 400` 且不回显；App Token 由 FastAPI 中间件校验 `X-App-Token` 请求头（环境变量 `FIELDWORK_APP_TOKEN` 未配置则完全开放），前端 localStorage 存令牌并经 `authHeaders()` 统一注入 rest.ts / sse.ts；`FlyAIClient._run_command` 以 `env` 参数向子进程注入 `FLYAI_API_KEY`；新增 `/ready` 端点以 blocking 检查（flyai CLI + 项目目录可写）区分「页面在线」与「可服务」，deploy.sh 据此 fail-hard。

**Tech Stack:** Python 3.10+ / FastAPI / Starlette 中间件 / pydantic / pytest（httpx.ASGITransport、asyncio_mode=auto）；前端 React 18 + TypeScript + Vite + vitest（jsdom）。

**Spec:** `docs/reviews/2026-10-16-项目开发现状审计.md`（F01/F02/F05/F15 四项及「第一批验收标准」）。

## Global Constraints

- **kernel 分层**：`kernel/` 仅依赖标准库与 pydantic，禁止 import `core`/`cli`/`mcp_server`/`web_host`（本计划新增的 `kernel/repository/guard.py` 只 import `re` 与 `typing`）。
- **绝对导入**：包内跨模块导入一律 `from fieldwork_agent.<pkg>.<mod> import ...`。
- **Web 测试模式**：httpx `ASGITransport` 不触发 lifespan，app fixture 必须手动调 `init_deps(app, config=..., repo=...)`（照抄 `tests/test_web_projects_api.py:7-23` 的既有模式）。
- **异步测试无装饰器**：pytest 配置 `asyncio_mode=auto`，`async def test_x(client):` 直接写、不加 `@pytest.mark.asyncio`。
- **错误折叠**：MCP 工具错误折 `{"ok": False, "error": {...}}` 包络、不抛协议异常；Web 折 `fastapi.HTTPException`。
- **Web 错误消息不回显用户原始输入**（浏览器反射面）；MCP 保留回显（CLI 侧诊断）——两侧策略刻意不同。
- **提交纪律**：每任务一次提交，中文消息，`feat:`/`fix:`/`refactor:`/`test:`/`chore:` 前缀。
- **命令工作目录**：Python 命令在仓库根 `f:\Agents_dev\Fieldwork_Agent`（PowerShell）执行；前端命令先 `cd web`。

---

### Task 1: kernel 路径 ID 守卫（单一实现来源）

**Files:**
- Create: `kernel/repository/guard.py`
- Test: `tests/test_kernel_guard.py`

**Interfaces:**
- Consumes: 无（本计划第一块基石）。
- Produces（Task 2/3 消费，签名精确）:
  - `path_id_violation(value, *, field="project_id", root=None, max_len=64) -> str` —— 非法返回原因文案，合法返回空串；`root` 非 None 时做 containment 兜底。
  - `project_id_violation(project_id, repo=None) -> str` —— 从 `repo.root`（`JsonRepository` 的 property）取根目录。
  - `turn_id_violation(turn_id) -> str` —— 16 位小写十六进制。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_kernel_guard.py`：

```python
# tests/test_kernel_guard.py
"""kernel 路径 ID 守卫单元测试（审计 F01 单一实现来源）。"""

from pathlib import Path

import pytest

from fieldwork_agent.kernel.repository.guard import (
    path_id_violation,
    project_id_violation,
    turn_id_violation,
)


class _Repo:
    """最小仓库替身：只为提供 root 属性（Protocol 替身无 root 时只靠字符校验）。"""

    def __init__(self, root: Path):
        self.root = root


@pytest.mark.parametrize("bad", [
    "",                                   # 空串
    "../../evil_rel",                     # 相对穿越
    "..\\..\\evil",                       # 反斜杠穿越
    "sub/dir", "sub\\dir",                # 内嵌分隔符
    "C:evil", "c:\\windows",             # 盘符（含盘符相对路径）
    ".", "..",                            # 相对路径段（整值匹配，非子串）
    "a\x00b",                             # null 字节
    "x" * 65,                             # 超长（上限 64）
])
def test_path_id_violation_rejects(bad):
    assert path_id_violation(bad) != ""


@pytest.mark.parametrize("bad_type", [123, None, [], {}])
def test_path_id_violation_rejects_non_string(bad_type):
    assert path_id_violation(bad_type) != ""


@pytest.mark.parametrize("good", [
    "p1",
    "p-1_2",
    "study..2024",                        # 语义修正钉住：整值匹配，合法名含子串 .. 不误拒
    "field_20261016_120000",
    "x" * 64,                             # 恰好上限
])
def test_path_id_violation_accepts(good):
    assert path_id_violation(good) == ""


def test_project_id_violation_uses_repo_root(tmp_path):
    repo = _Repo(tmp_path)
    assert project_id_violation("normal", repo) == ""
    assert project_id_violation("../../evil_rel", repo) != ""


def test_containment_escape_via_symlink(tmp_path):
    outside = tmp_path.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "lnk"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("本环境无法创建符号链接（Windows 需开发者模式/管理员）")
    repo = _Repo(tmp_path)
    # "lnk" 是合法单段名，但 resolve 后指向根外——containment 兜底应拒绝
    assert project_id_violation("lnk", repo) != ""


def test_turn_id_violation():
    assert turn_id_violation("0123456789abcdef") == ""
    assert turn_id_violation("zzz") != ""
    assert turn_id_violation("0123456789ABCDEF") != ""    # 大写拒绝
    assert turn_id_violation("0123456789abcdef\n") != ""   # 尾部换行拒绝（fullmatch）
    assert turn_id_violation(123) != ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_kernel_guard.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'fieldwork_agent.kernel.repository.guard'`

- [ ] **Step 3: 写实现**

创建 `kernel/repository/guard.py`：

```python
"""路径 ID 守卫（审计 F01）：project_id / turn_id 校验的单一实现来源。

mcp_server（折 tool 错误包络）与 web_host（折 HTTPException）两个外壳薄壳
复用，规则变更只改本文件。kernel 只依赖标准库。
"""

import re
from typing import Any

PID_MAX_LEN = 64
_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")
TURN_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def path_id_violation(
    value: Any,
    *,
    field: str = "project_id",
    root: Any = None,
    max_len: int = PID_MAX_LEN,
) -> str:
    """用作路径段的 ID 非法则返回原因，合法返回空串。

    两层防御：

    1. 字符校验——拒绝非字符串/空串、超长、``/``、``\\``、null 字节、
       整值 ``.``/``..``（不拒绝 ``study..2024`` 这类含子串的合法名）、
       Windows 盘符模式（``C:`` 开头，含盘符相对路径 ``C:evil``）；
    2. containment 双保险——root 非 None 时要求 ``(root / value).resolve()``
       仍落在 ``root.resolve()`` 之内（防项目目录内预置符号链接逃逸）。
    """
    if not isinstance(value, str) or not value:
        return f"{field} 须为非空字符串"
    if len(value) > max_len:
        return f"{field} 长度不得超过 {max_len}"
    if "/" in value or "\\" in value:
        return f"{field} 不得含路径分隔符（/ 或 \\）"
    if "\x00" in value:
        return f"{field} 不得含 null 字节"
    if value == "." or value == "..":
        return f"{field} 不得为相对路径段（. 或 ..）"
    if _DRIVE_LETTER_RE.match(value):
        return f"{field} 不得为 Windows 盘符路径"
    if root is not None:
        try:
            if not (root / value).resolve().is_relative_to(root.resolve()):
                return f"{field} 不得指向根目录之外"
        except (ValueError, OSError):
            return f"{field} 含无法解析的路径成分"
    return ""


def project_id_violation(project_id: Any, repo: Any = None) -> str:
    """project_id 专用：repo 带 root 属性（JsonRepository 的 property）则做 containment。"""
    return path_id_violation(
        project_id, field="project_id", root=getattr(repo, "root", None)
    )


def turn_id_violation(turn_id: Any) -> str:
    """turn_id 专用：16 位小写十六进制（服务端 secrets.token_hex(8) 生成格式）。"""
    if not isinstance(turn_id, str) or not TURN_ID_RE.fullmatch(turn_id):
        return "turn_id 须为 16 位小写十六进制（服务端生成格式）"
    return ""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_kernel_guard.py -v`
Expected: PASS（符号链接用例在 Windows 无权限环境下 SKIP 属正常）

- [ ] **Step 5: 提交**

```bash
git add kernel/repository/guard.py tests/test_kernel_guard.py
git commit -m "feat(kernel): 路径 ID 守卫单一来源（审计 F01 基座，.. 整值匹配语义修正）"
```

---

### Task 2: MCP state_tools 薄壳化改接 kernel 守卫

**Files:**
- Modify: `mcp_server/state_tools.py:13`（删 `import re`）、`:23-28`（常量块）、`:45-86`（删 `_pid_violation`、改写 `guard_project_id`）
- Test: `tests/test_mcp_state_tools.py`（文件末尾追加用例）

**Interfaces:**
- Consumes: Task 1 的 `project_id_violation(project_id, repo=None) -> str`。
- Produces: `guard_project_id(project_id, repo) -> dict | None` —— 签名与返回包络形状保持不变（`mcp_server/resources.py:16` 的 import 与 `:288` 的调用零改动）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_mcp_state_tools.py` 末尾追加（该文件已有模块级 `make_repo` 辅助函数与 `build_handlers` 导入，直接复用）：

```python
def test_double_dot_inside_pid_now_accepted(tmp_path):
    """审计 F01 语义修正：整值匹配 . / ..，合法名含子串 .. 不再误拒。"""
    h = build_handlers(make_repo(tmp_path))
    out = h["create_project"](project_id="study..2024")
    assert out["ok"] is True
    assert out["data"]["project_id"] == "study..2024"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_mcp_state_tools.py::test_double_dot_inside_pid_now_accepted -v`
Expected: FAIL —— `out["ok"]` 为 False（现行 `".." in project_id` 子串匹配把 `study..2024` 折成 `invalid_args`）

- [ ] **Step 3: 改写 state_tools.py**

四处修改：

(a) 删除第 13 行 `import re`（改后全文件不再直接用 re）。

(b) 常量块（现 23-28 行）替换为：

```python
ITINERARY_STATE_KEYS = ("itinerary", "travel_plan")   # 兼容旧键

# project_id 净化规则（终审 C1）：规则实现已下沉 kernel/repository/guard.py
# （审计 F01 单一来源），本模块只留提示文案用于 MCP 错误包络折叠。
PID_RULE_HINT = "project_id 仅允许字母数字下划线连字符等安全字符"
```

(c) 导入区在 `from fieldwork_agent.kernel.repository.protocol import ProjectRepository` 之前插入一行：

```python
from fieldwork_agent.kernel.repository.guard import project_id_violation
```

(d) 删除整个 `_pid_violation` 函数（现 45-73 行），`guard_project_id` 改写为：

```python
def guard_project_id(project_id: Any, repo: Any = None) -> dict | None:
    """统一净化守卫：非法 project_id → ``invalid_args`` 错误包络；合法 → None。

    规则实现于 kernel/repository/guard.py（审计 F01 单一来源）；本函数只折
    MCP 错误包络并回显原始输入（CLI 侧诊断用——与 Web 侧不回显策略不同）。
    ``resources.read_resource`` 的项目类 URI 路由复用本函数。
    """
    reason = project_id_violation(project_id, repo)
    if not reason:
        return None
    return _err("invalid_args", f"非法 project_id: {project_id!r}（{reason}）",
                PID_RULE_HINT)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_mcp_state_tools.py::test_double_dot_inside_pid_now_accepted -v`
Expected: PASS

- [ ] **Step 5: MCP 全量回归**

Run: `python -m pytest tests/test_mcp_state_tools.py tests/test_mcp_resources_prompts.py tests/test_mcp_dispatch.py tests/test_mcp_server_contract.py -q`
Expected: 全部 PASS（现有守卫用例的输入全部含分隔符/盘符/整值段，整值匹配不影响其通过）

- [ ] **Step 6: 提交**

```bash
git add mcp_server/state_tools.py tests/test_mcp_state_tools.py
git commit -m "refactor(mcp): state_tools 改接 kernel 守卫，.. 子串误拒修正（F01）"
```

---

### Task 3: Web 守卫接线（F01 封堵 Web 入口）

**Files:**
- Create: `web_host/api/guard.py`
- Modify: `web_host/api/projects.py:19-27`（create_project）、`:43-48`（get_project）
- Modify: `web_host/api/turns.py:69-84`（create_turn）、`:97-128`（stream_events）、`:131-138`（cancel_turn）
- Modify: `web_host/api/exports.py`（3 个路由函数体首行）
- Test: `tests/test_web_guard.py`

**Interfaces:**
- Consumes: Task 1 的 `project_id_violation` / `turn_id_violation`。
- Produces（Web API 错误契约）:
  - `checked_project_id(project_id: str, repo=None) -> str` —— 非法抛 `HTTPException(400, detail={"code": "invalid_project_id", "message": ...})`，合法原样返回。
  - `checked_turn_id(turn_id: str) -> str` —— 非法抛 `HTTPException(400, detail={"code": "invalid_turn_id", "message": ...})`，合法原样返回。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_web_guard.py`：

```python
# tests/test_web_guard.py
"""审计 F01：Web 项目/回合 ID 守卫（目录穿越封堵）。"""

import pytest
from httpx import ASGITransport, AsyncClient

from fieldwork_agent.web_host.app import create_app


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    _app = create_app(config=None, repo=repo)
    # ASGITransport 不触发 lifespan，手动初始化依赖
    init_deps(_app, config=None, repo=repo)
    return _app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_create_traversal_pid_rejected_no_side_effect(client, tmp_path):
    r = await client.post("/projects", json={"project_id": "../../evil_rel"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_project_id"
    # 审计验收：恶意 ID 无越界副作用
    assert not (tmp_path.parent / "evil_rel").exists()
    assert list(tmp_path.rglob("*evil*")) == []
    lst = await client.get("/projects")
    assert lst.json() == []


async def test_create_other_malformed_pids_rejected(client):
    for bad in ("..\\..\\evil", "sub\\dir", "C:evil"):
        r = await client.post("/projects", json={"project_id": bad})
        assert r.status_code == 400, bad


async def test_get_traversal_pid_rejected(client):
    r = await client.get("/projects/C:evil")
    assert r.status_code == 400


async def test_export_traversal_pid_rejected(client):
    r = await client.get("/projects/C:evil/proposal")
    assert r.status_code == 400


async def test_turn_guard_precedes_auth(client):
    # 守卫 400 应先于 _resolve_clients 的 403 llm_key_required（语义错误先于鉴权报告）
    r = await client.post("/projects/C:evil/messages", json={"text": "hi"})
    assert r.status_code == 400


async def test_turn_id_guard(client):
    await client.post("/projects", json={"project_id": "p1"})
    r = await client.get("/projects/p1/turns/zzz/events")
    assert r.status_code == 400
    r = await client.post("/projects/p1/turns/zzz/cancel")
    assert r.status_code == 400


async def test_double_dot_inside_pid_accepted(client):
    """整值匹配语义：study..2024 合法（与 MCP 侧行为一致）。"""
    r = await client.post("/projects", json={"project_id": "study..2024"})
    assert r.status_code == 201
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_web_guard.py -v`
Expected: FAIL —— 穿越类 POST 得 201（创建了目录）或 GET 得 404，均非 400

- [ ] **Step 3: 写实现并接线**

(a) 创建 `web_host/api/guard.py`：

```python
"""Web 宿主的 ID 守卫薄壳：kernel 规则 → HTTPException 400。

审计 F01：错误消息不回显原始输入（浏览器反射面），与 MCP 侧（CLI 诊断、
保留回显）策略刻意不同。
"""

from typing import Any

from fastapi import HTTPException

from fieldwork_agent.kernel.repository.guard import (
    project_id_violation,
    turn_id_violation,
)


def checked_project_id(project_id: str, repo: Any = None) -> str:
    """非法 project_id → 400 invalid_project_id；合法原样返回。"""
    reason = project_id_violation(project_id, repo)
    if reason:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_project_id", "message": f"非法 project_id（{reason}）"},
        )
    return project_id


def checked_turn_id(turn_id: str) -> str:
    """非法 turn_id → 400 invalid_turn_id；合法原样返回。"""
    reason = turn_id_violation(turn_id)
    if reason:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_turn_id", "message": f"非法 turn_id（{reason}）"},
        )
    return turn_id
```

(b) `web_host/api/projects.py`：导入区加 `from fieldwork_agent.web_host.api.guard import checked_project_id`；`create_project` 在 `pid = body.project_id or f"field_{datetime.now():%Y%m%d_%H%M%S}"` 之后、`repo.create(...)` 之前加：

```python
    checked_project_id(pid, repo)   # 审计 F01：守卫先于一切副作用
```

`get_project` 函数体首行（`repo.get_state(...)` 所在 try 之前）加：

```python
    checked_project_id(project_id, repo)
```

(c) `web_host/api/turns.py`：导入区加 `from fieldwork_agent.web_host.api.guard import checked_project_id, checked_turn_id`。

`create_turn` 在 `repo = get_repo(request)` 之后、`repo.get_state(...)` 之前加（保证 400 先于 404/403）：

```python
    checked_project_id(project_id, repo)
```

`stream_events` 函数体首部（现有 `tm = get_turn_manager(request)` 之前）加：

```python
    repo = get_repo(request)
    checked_project_id(project_id, repo)
    checked_turn_id(turn_id)
```

`cancel_turn` 同样在函数体首部加以上三行（`get_repo` 已在模块导入区，直接用）。

(d) `web_host/api/exports.py`：导入区加 `from fieldwork_agent.web_host.api.guard import checked_project_id`；3 个路由（`get_proposal`、`download_proposal`、`list_proposals`）函数体首行各加：

```python
    checked_project_id(project_id, repo)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_web_guard.py -v`
Expected: PASS

- [ ] **Step 5: Web 全量回归**

Run: `python -m pytest tests/test_web_ -q`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add web_host/api/guard.py web_host/api/projects.py web_host/api/turns.py web_host/api/exports.py tests/test_web_guard.py
git commit -m "fix(web): 项目/回合 ID 守卫接线，封堵目录穿越（审计 F01）"
```

---

### Task 4: App Token 后端（F02）

**Files:**
- Modify: `core/config.py:29-30`（`flyai_api_key` 之后加 `app_token`）
- Modify: `tests/conftest.py:22`（FakeConfig 加 `app_token`）
- Modify: `web_host/app.py`（imports、模块级 helpers、`create_app` 内中间件、`/health` 路由）
- Test: `tests/test_web_app_token.py`

**Interfaces:**
- Consumes: 无。
- Produces（Task 5 前端消费）:
  - `Config.app_token: str` —— 读环境变量 `FIELDWORK_APP_TOKEN`，不进 `save()`/`_load_config`（不落盘）。
  - 中间件行为：`app.state.config.app_token` 非空时，`/projects`、`/settings` 前缀（含子路径）缺或错 `X-App-Token` → 401 `{"code": "app_token_required", ...}`；未配置则完全开放。
  - `/health` 返回 `{"status": "ok", "auth_required": bool}` —— 前端据此切换令牌输入框提示。
  - 模块常量 `APP_TOKEN_HEADER = "X-App-Token"`（Task 5 前端字面量与此对齐）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_web_app_token.py`：

```python
# tests/test_web_app_token.py
"""审计 F02：App Token 访问控制（后端中间件）。"""

import pytest
from httpx import ASGITransport, AsyncClient

from fieldwork_agent.web_host.app import create_app


@pytest.fixture
def protected_app(tmp_path, fake_config):
    fake_config.app_token = "secret-token"
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    _app = create_app(config=fake_config, repo=repo)
    init_deps(_app, config=fake_config, repo=repo)
    return _app


@pytest.fixture
async def client(protected_app):
    transport = ASGITransport(app=protected_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_missing_token_rejected(client):
    r = await client.get("/projects")
    assert r.status_code == 401
    assert r.json()["code"] == "app_token_required"


async def test_wrong_token_rejected(client):
    r = await client.get("/projects", headers={"X-App-Token": "wrong"})
    assert r.status_code == 401


async def test_correct_token_accepted(client):
    r = await client.get("/projects", headers={"X-App-Token": "secret-token"})
    assert r.status_code == 200


async def test_settings_protected_too(client):
    r = await client.get("/settings/status")
    assert r.status_code == 401


async def test_health_exempt_and_reports_auth(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "auth_required": True}


async def test_unconfigured_token_means_open(tmp_path, fake_config):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    _app = create_app(config=fake_config, repo=repo)
    init_deps(_app, config=fake_config, repo=repo)
    async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as c:
        r = await c.get("/projects")
        assert r.status_code == 200
        h = await c.get("/health")
        assert h.json()["auth_required"] is False


async def test_cors_preflight_passes_with_token_enabled(client):
    r = await client.options("/projects", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-app-token",
    })
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") is not None


async def test_401_carries_cors_headers(client):
    r = await client.get("/projects", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_web_app_token.py -v`
Expected: FAIL —— `/projects` 返回 200 而非 401；`/health` 响应无 `auth_required` 键

- [ ] **Step 3: 写实现**

(a) `core/config.py` 在 `self.flyai_api_key: str = os.getenv("FLYAI_API_KEY", "")` 之后加：

```python
        # 应用访问令牌（审计 F02）：非空时 Web 宿主中间件要求 X-App-Token 匹配。
        # 仅环境变量——不进 save()/_load_config（令牌属部署边界，非用户偏好，不落盘）。
        self.app_token: str = os.getenv("FIELDWORK_APP_TOKEN", "")
```

(b) `tests/conftest.py` FakeConfig 的 `self.flyai_api_key = ""` 之后加：

```python
        self.app_token = ""   # 审计 F02：App Token 守卫读取（空 = 不启用）
```

(c) `web_host/app.py` 顶部 import 区做三处修改：`from contextlib import asynccontextmanager` 之前加 `import hmac`；`from fastapi import FastAPI` 改为 `from fastapi import FastAPI, Request`；`from fastapi.middleware.cors import CORSMiddleware` 之后加 `from fastapi.responses import JSONResponse`。

模块级（`create_app` 定义之前）加：

```python
APP_TOKEN_HEADER = "X-App-Token"
# 受保护前缀（审计 F02）：/projects、/settings 全族；/health、/ready 与静态前端
# 豁免——页面要先能打开、健康探针不该被令牌拦住。
_PROTECTED_PREFIXES = ("/projects", "/settings")


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
```

(d) `create_app` 内 `app = FastAPI(title="Fieldwork Web Host", version="0.1.0", lifespan=lifespan)` 之后、`app.add_middleware(CORSMiddleware, ...)` **之前**插入（代码顺序是正确性的组成部分，注释勿删）：

```python
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
```

(e) `/health` 路由改为：

```python
    @app.get("/health")
    async def health(request: Request):
        cfg = getattr(request.app.state, "config", None)
        return {"status": "ok", "auth_required": bool(getattr(cfg, "app_token", "") or "")}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_web_app_token.py -v`
Expected: PASS

- [ ] **Step 5: Web 全量回归（中间件与 SSE 流式兼容）**

Run: `python -m pytest tests/test_web_ -q`
Expected: 全部 PASS——特别注意 `tests/test_web_turns_api.py`（SSE 流式路由经 `@app.middleware("http")` 的 BaseHTTPMiddleware 仍正常流式输出）

- [ ] **Step 6: 提交**

```bash
git add core/config.py tests/conftest.py web_host/app.py tests/test_web_app_token.py
git commit -m "feat(web): F02 后端 App Token 访问控制（X-App-Token 中间件 + /health auth_required）"
```

---

### Task 5: App Token 前端（设置卡第三输入 + 双通道请求头注入）

**Files:**
- Modify: `web/src/lib/keys.ts`（全量替换：`ApiKeys` 增 `appToken`、`saveKeys` 增可选第三参、`authHeaders` 增 `X-App-Token`）
- Modify: `web/src/api/sse.ts:1`（补 import）、`:57`（headers 展开 `authHeaders()`）
- Modify: `web/src/api/rest.ts`（`HealthStatus` 接口 + `rest.health()`）
- Modify: `web/src/components/settings/ApiKeyCard.tsx`（访问令牌第三输入框 + 徽标）
- Test: `web/tests/keys.spec.ts`（新建；`web/tests/e2e/keys.spec.ts` 是 Playwright e2e、已被 vitest 配置排除，同名不冲突）

**Interfaces:**
- Consumes: Task 4 的 `/health` 响应 `{"status": "ok", "auth_required": bool}`；请求头名 `APP_TOKEN_HEADER = "X-App-Token"`（前端字面量与此对齐）。
- Produces:
  - `interface ApiKeys { dashscope: string; flyai: string; appToken: string }`
  - `saveKeys(dashscope: string, flyai: string, appToken?: string): void` —— 第三参 `undefined` 表示「不动已存令牌」，既有两参调用点零改动。
  - `authHeaders(): Record<string, string>` —— 空值不带头；令牌缺失即匿名（后端未配置时照常放行）。
  - `interface HealthStatus { status: string; auth_required: boolean }`、`rest.health(): Promise<HealthStatus>`。

- [ ] **Step 1: 写失败测试**

创建 `web/tests/keys.spec.ts`：

```typescript
// web/tests/keys.spec.ts
import { beforeEach, describe, expect, it, vi } from "vitest";
import { authHeaders, clearKeys, getKeys, saveKeys } from "../src/lib/keys";
import { streamTurnEvents } from "../src/api/sse";

describe("keys", () => {
  beforeEach(() => localStorage.clear());

  it("saveKeys 三参写入并可读回", () => {
    saveKeys("sk-a", "fly-b", "tok-c");
    expect(getKeys()).toEqual({ dashscope: "sk-a", flyai: "fly-b", appToken: "tok-c" });
  });

  it("两参调用不动已存令牌（向后兼容）", () => {
    saveKeys("sk-a", "", "tok-c");
    saveKeys("sk-d", "");
    expect(getKeys().appToken).toBe("tok-c");
    expect(getKeys().dashscope).toBe("sk-d");
  });

  it("authHeaders 注入三个头；未存不带 X-App-Token", () => {
    saveKeys("sk-a", "", "tok-c");
    expect(authHeaders()).toEqual({ "X-DashScope-Key": "sk-a", "X-App-Token": "tok-c" });
    clearKeys();
    expect(authHeaders()).toEqual({});
  });

  it("clearKeys 清三项", () => {
    saveKeys("sk-a", "fly-b", "tok-c");
    clearKeys();
    expect(getKeys()).toEqual({ dashscope: "", flyai: "", appToken: "" });
  });

  it("streamTurnEvents 携带 App Token 头", async () => {
    saveKeys("", "", "secret-token");
    const inits: Array<RequestInit | undefined> = [];
    const resp = {
      ok: true,
      body: {
        getReader: () => ({
          read: async () => ({ value: undefined, done: true }),
          cancel: async () => {},
        }),
      },
    };
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      inits.push(init ?? {});
      return resp as unknown as Response;
    }));
    await streamTurnEvents("p1", "0123456789abcdef", { onEvent: () => {} });
    expect(inits[0].headers).toMatchObject({
      Accept: "text/event-stream",
      "X-App-Token": "secret-token",
    });
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web; npx vitest run tests/keys.spec.ts`
Expected: FAIL —— `getKeys()` 无 `appToken` 键（toEqual 失败）；SSE 用例 headers 中无 `X-App-Token`

- [ ] **Step 3: 全量替换 `web/src/lib/keys.ts`**

```typescript
/** 页面级 API key 与访问令牌：localStorage 读写 + 请求头注入。
 *
 * 首页「API 密钥」卡保存；rest.ts / sse.ts 每次请求自动带 X-DashScope-Key /
 * X-FlyAI-Key / X-App-Token（后端 web_host/api/turns.py 读取页面 key，
 * web_host/app.py 中间件校验访问令牌——审计 F02）。
 * key 只存在浏览器本地，不落服务端。
 */

const DASHSCOPE_KEY = "fieldwork.dashscope_key";
const FLYAI_KEY = "fieldwork.flyai_key";
const APP_TOKEN_KEY = "fieldwork.app_token";

export interface ApiKeys {
  dashscope: string;
  flyai: string;
  appToken: string;
}

function read(name: string): string {
  try {
    return (localStorage.getItem(name) || "").trim();
  } catch {
    return ""; // localStorage 不可用（隐私模式等）时按无 key 处理
  }
}

export function getKeys(): ApiKeys {
  return { dashscope: read(DASHSCOPE_KEY), flyai: read(FLYAI_KEY), appToken: read(APP_TOKEN_KEY) };
}

export function hasDashScopeKey(): boolean {
  return read(DASHSCOPE_KEY) !== "";
}

/** appToken 传 undefined 表示"不修改已存令牌"（向后兼容旧调用点）。 */
export function saveKeys(dashscope: string, flyai: string, appToken?: string): void {
  const d = dashscope.trim();
  if (d) localStorage.setItem(DASHSCOPE_KEY, d);
  else localStorage.removeItem(DASHSCOPE_KEY); // 留空即移除，回落服务端配置
  const f = flyai.trim();
  if (f) localStorage.setItem(FLYAI_KEY, f);
  else localStorage.removeItem(FLYAI_KEY);
  if (appToken !== undefined) {
    const t = appToken.trim();
    if (t) localStorage.setItem(APP_TOKEN_KEY, t);
    else localStorage.removeItem(APP_TOKEN_KEY);
  }
}

export function clearKeys(): void {
  localStorage.removeItem(DASHSCOPE_KEY);
  localStorage.removeItem(FLYAI_KEY);
  localStorage.removeItem(APP_TOKEN_KEY);
}

/** 注入到 fetch headers 的鉴权头；空值不带头（key 回落服务端，令牌回落匿名）。 */
export function authHeaders(): Record<string, string> {
  const { dashscope, flyai, appToken } = getKeys();
  const h: Record<string, string> = {};
  if (dashscope) h["X-DashScope-Key"] = dashscope;
  if (flyai) h["X-FlyAI-Key"] = flyai;
  if (appToken) h["X-App-Token"] = appToken;
  return h;
}
```

- [ ] **Step 4: sse.ts / rest.ts / ApiKeyCard.tsx 接线**

(a) `web/src/api/sse.ts`：第 1 行 `import type { EventEnvelope } from "../types/events";` 之后加：

```typescript
import { authHeaders } from "../lib/keys";
```

第 57 行（构造 headers 处）改为：

```typescript
    const headers: Record<string, string> = { Accept: "text/event-stream", ...authHeaders() };
```

(b) `web/src/api/rest.ts`：`SettingsStatus` 接口定义之后加：

```typescript
/** GET /health 的返回（web_host/app.py：auth_required 表示服务端启用了 App Token）。 */
export interface HealthStatus {
  status: string;
  auth_required: boolean;
}
```

`rest` 对象中 `settingsStatus` 之后加一个方法：

```typescript
  health: () => req<HealthStatus>("/health"),
```

（`/health` 已在 Task 4 中间件的豁免名单内，匿名可访问。）

(c) `web/src/components/settings/ApiKeyCard.tsx` 九处修改：

1. import 行改为：

```tsx
import { rest, type HealthStatus, type SettingsStatus } from "../../api/rest";
```

2. `keyBadge` 函数定义之后加：

```tsx
/** 访问令牌徽标（审计 F02）：服务端未启用 / 浏览器已存 / 服务端要求且未填 */
function tokenBadge(local: boolean, required?: boolean): { text: string; cls: string } {
  if (!required) return { text: "未启用", cls: "text-inkfaint" };
  if (local) return { text: "浏览器已存", cls: "text-bamboo" };
  return { text: "需要令牌", cls: "text-cinnabar" };
}
```

3. state 区加：

```tsx
  const [appToken, setAppToken] = useState("");
  const [health, setHealth] = useState<HealthStatus | null>(null);
```

4. `useEffect` 中 `getKeys()` 读取处加 `setAppToken(k.appToken);`；再追加一次探询：

```tsx
    rest.health().then(setHealth).catch(() => setHealth(null));
```

5. `save()` 中 `saveKeys(dashscope, flyai)` 改为 `saveKeys(dashscope, flyai, appToken);`

6. `clear()` 中 `clearKeys()` 之后加 `setAppToken("");`

7. `const ds = keyBadge(...)` 一行之后加：

```tsx
  const tk = tokenBadge(appToken.trim() !== "", health?.auth_required);
```

8. 第二个 `</label>`（FlyAI 输入框）之后、按钮区 `<div>` 之前插入第三个输入框：

```tsx
        <label className="block mb-3.5">
          <span className="flex items-center justify-between text-[11.5px] text-inksoft mb-1">
            访问令牌（App Token）
            <span className={tk.cls}>{tk.text}</span>
          </span>
          <input
            type="password"
            value={appToken}
            onChange={(e) => {
              setAppToken(e.target.value);
              setJustSaved(false);
            }}
            placeholder={health?.auth_required ? "服务端已启用，必填" : "服务端未启用，可留空"}
            autoComplete="off"
            className="w-full hairline rounded-md bg-paper/60 px-3 h-8 text-[12.5px] text-ink outline-none placeholder:text-inkfaint"
          />
        </label>
```

9. 卡片底部说明 `<p>` 内追加一句：「公网部署时服务端可设 FIELDWORK_APP_TOKEN，此处填写同一令牌。」

- [ ] **Step 5: 跑测试确认通过**

Run: `cd web; npx vitest run tests/keys.spec.ts`
Expected: PASS（5 个用例全绿）

- [ ] **Step 6: 前端全量回归**

Run: `cd web; npm test`
Expected: 全部 PASS——既有用例若渲染 `ApiKeyCard`，因令牌输入框初始为空、徽标走「未启用」分支，行为不变；`sse.spec.ts` 的 fetch stub 不受新增 `X-App-Token` 头影响（既有断言不检查该头）

- [ ] **Step 7: 提交**

```bash
git add web/src/lib/keys.ts web/src/api/sse.ts web/src/api/rest.ts web/src/components/settings/ApiKeyCard.tsx web/tests/keys.spec.ts
git commit -m "feat(web): F02 前端访问令牌——设置卡第三输入 + 双通道请求头注入"
```

---

### Task 6: FlyAI 子进程注入页面级 Key（F05）

**Files:**
- Modify: `kernel/sources/flyai.py`（import 区加 `os`；`_run_command` 组 env 传给 subprocess.run）
- Test: `tests/test_sources_flyai.py`（末尾追加两个用例）

**Interfaces:**
- Consumes: 无新接口——`web_host/api/turns.py` 已以页面 key 构造回合级 `FlyAIClient(api_key=...)`，本任务只补「实例 key → 子进程 env」最后一环。
- Produces: `_run_command` 行为——`self.api_key` 非空时 `env={**os.environ, "FLYAI_API_KEY": self.api_key}`（flyai CLI 解析链中 env 优先级最高，注入即覆盖）；为空时 `env=None`（继承父进程环境，CLI 回落自身配置文件）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_sources_flyai.py` 末尾追加：

```python
def test_run_command_injects_page_level_key(monkeypatch):
    """审计 F05：回合级实例（带页面 key）把 FLYAI_API_KEY 注入子进程 env。"""
    import os as _os

    from fieldwork_agent.kernel.sources import flyai as flyai_mod

    captured: dict = {}

    class _Result:
        returncode = 0
        stdout = '{"status": 0, "data": {"itemList": []}}'
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr(flyai_mod.subprocess, "run", fake_run)
    client = flyai_mod.FlyAIClient(api_key="page-key")
    out = client._run_command(["searchHotels", "--dest", "大理"])
    assert out["status"] == 0
    assert captured["env"]["FLYAI_API_KEY"] == "page-key"
    assert captured["env"]["PATH"] == _os.environ["PATH"]  # 继承而非替换整个环境


def test_run_command_no_key_passes_env_none(monkeypatch):
    """服务端单例（无 key）不动子进程 env——CLI 回落自身配置文件。"""
    from fieldwork_agent.kernel.sources import flyai as flyai_mod

    captured: dict = {}

    class _Result:
        returncode = 0
        stdout = '{"status": 0, "data": {"itemList": []}}'
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr(flyai_mod.subprocess, "run", fake_run)
    client = flyai_mod.FlyAIClient(api_key="")
    client._run_command(["ping"])
    assert captured["env"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_sources_flyai.py::test_run_command_injects_page_level_key tests/test_sources_flyai.py::test_run_command_no_key_passes_env_none -v`
Expected: FAIL —— `KeyError: 'env'`（subprocess.run 尚未收到 env 参数）

- [ ] **Step 3: 写实现**

`kernel/sources/flyai.py` 两处修改：

(a) import 区在 `import json` 与 `import subprocess` 之间插入一行 `import os`（保持字母序）。

(b) `_run_command` 中 `subprocess.run(...)` 调用之前加：

```python
        # 审计 F05：页面级 key（回合级实例化）注入子进程 env——
        # flyai CLI 解析链 env.FLYAI_API_KEY 优先级最高，注入即覆盖服务端单例配置。
        env = {**os.environ, "FLYAI_API_KEY": self.api_key} if self.api_key else None
```

并给 `subprocess.run(...)` 调用加参数 `env=env`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_sources_flyai.py -q`
Expected: 全部 PASS（既有用例若对 subprocess.run 的调用参数有断言而失败，同步补 env 断言——先跑再看）

- [ ] **Step 5: 提交**

```bash
git add kernel/sources/flyai.py tests/test_sources_flyai.py
git commit -m "fix(kernel): FlyAI 子进程注入页面级 FLYAI_API_KEY（审计 F05）"
```

---

### Task 7: /ready 就绪端点（区分「页面在线」与「可服务」）

**Files:**
- Modify: `web_host/app.py`（顶部 import 区加 `import shutil`；`/health` 路由之后加 `/ready`）
- Test: `tests/test_web_ready.py`

**Interfaces:**
- Consumes: `app.state.repo`（`root` 属性）、`app.state.config`、`app.state.llm`（`init_deps` 已设置；ASGITransport 测试须手动 init_deps——照抄 Global Constraints 模式）。
- Produces（Task 8 deploy.sh 消费）: `GET /ready` → 200 `{"status": "ready", "checks": {"flyai_cli": bool, "projects_dir_writable": bool, "dashscope_configured": bool, "flyai_key_configured": bool}}` 或 503 `{"status": "not_ready", "checks": {...}}`；blocking 项仅 `flyai_cli` 与 `projects_dir_writable`，密钥类只报告不阻断（未配 key 时对话按回合报 `llm_key_required`，不算服务不可用）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_web_ready.py`：

```python
# tests/test_web_ready.py
"""审计 F15：/ready 就绪端点（blocking = flyai CLI + 项目目录可写）。"""

import pytest
from httpx import ASGITransport, AsyncClient

from fieldwork_agent.web_host.app import create_app


@pytest.fixture
def app(tmp_path):
    from fieldwork_agent.kernel.repository.json_repo import JsonRepository
    from fieldwork_agent.web_host.deps import init_deps

    repo = JsonRepository(projects_dir=tmp_path / "projects")
    _app = create_app(config=None, repo=repo)
    init_deps(_app, config=None, repo=repo)
    return _app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_ready_ok(client, monkeypatch):
    # CI 机器通常没装 flyai CLI：monkeypatch 成假路径保证确定性
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/flyai" if name == "flyai" else None)
    r = await client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["flyai_cli"] is True
    assert body["checks"]["projects_dir_writable"] is True


async def test_ready_missing_flyai_cli_503(client, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    r = await client.get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["flyai_cli"] is False


async def test_ready_unwritable_projects_dir_503(client, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/flyai")

    def denied(self, *a, **k):
        raise OSError("read-only")

    monkeypatch.setattr("pathlib.Path.touch", denied)
    r = await client.get("/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["projects_dir_writable"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_web_ready.py -v`
Expected: FAIL —— 404（路由不存在）

- [ ] **Step 3: 写实现**

`web_host/app.py` 顶部 import 区加 `import shutil`；`/health` 路由之后加：

```python
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
            probe = root / ".ready_probe"
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
        blocking = ("flyai_cli", "projects_dir_writable")
        ok = all(checks[k] for k in blocking)
        return JSONResponse(
            content={"status": "ready" if ok else "not_ready", "checks": checks},
            status_code=200 if ok else 503,
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_web_ready.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add web_host/app.py tests/test_web_ready.py
git commit -m "feat(web): /ready 就绪端点（审计 F15，flyai CLI + 目录可写 blocking）"
```

---

### Task 8: 部署脚本 flyai CLI 依赖 + fail-hard 健康检查（F15）

**Files:**
- Modify: `deploy/aliyun/deploy.sh`（三处：步骤 2 加 curl、新步骤 4.5 装 flyai CLI、步骤 7 整块替换为 fail-hard）
- Modify: `deploy/aliyun/fieldwork-web.env.example`（追加 FIELDWORK_APP_TOKEN 注释段）
- Modify: `deploy/aliyun/DEPLOY.md`（三处：第 0 节依赖、第 4 节密钥、第 9 节排错表）

**Interfaces:**
- Consumes: Task 7 的 `/ready`（grep `"status":"ready"` 判定）；Task 4 的 `FIELDWORK_APP_TOKEN` 环境变量。
- Produces: 全新目标机器按 DEPLOY.md 可完成安装，且部署结束状态能区分「页面在线」（/health ok）与「搜索可用」（/ready ok）——审计 F15 验收。

- [ ] **Step 1: deploy.sh 三处修改**

(a) 步骤 2（现 36-38 行）改为：

```bash
dnf install -y python3-pip python3-devel gcc curl || { echo "!! dnf 安装失败，若已具备 gcc/python3-devel/curl 可忽略"; }
```

(b) 步骤 4 的 `fi` 之后、步骤 5 注释之前插入：

```bash
# 4.5) flyai CLI 检测（审计 F15：旅行搜索工具的运行时依赖，缺失只警告不阻塞——
#      末尾 /ready 会再次报告）
if command -v flyai >/dev/null 2>&1; then
  echo "==> flyai CLI 已安装: $(command -v flyai)"
elif command -v npm >/dev/null 2>&1; then
  echo "==> 检测到 npm，安装 @fly-ai/flyai-cli …"
  npm install -g @fly-ai/flyai-cli || echo "!! npm 安装失败，请手动执行: npm install -g @fly-ai/flyai-cli"
else
  echo "!! 未检测到 Node/npm。旅行搜索需要 flyai CLI，请手动安装:"
  echo "    dnf install -y nodejs npm && npm install -g @fly-ai/flyai-cli"
  echo "   （不阻塞本次部署；装好后 curl http://127.0.0.1:$PORT/ready 确认 flyai_cli=true）"
fi
```

(c) 步骤 7（现 73-82 行整块）替换为：

```bash
# 7) 本地冒烟（审计 F15：健康检查失败必须退出非零，不再吞错）
sleep 2
echo "==> 服务状态："
systemctl --no-pager --full status "$SVC" | head -n 12 || true
echo "==> 本地健康检查 http://127.0.0.1:$PORT/health ："
if ! command -v curl >/dev/null 2>&1; then
  echo "!! 未装 curl（步骤 2 已尝试安装），无法自动健康检查" >&2
  exit 1
fi
if ! curl -sf -m 5 "http://127.0.0.1:$PORT/health" >/dev/null; then
  echo "!! /health 探测失败——服务未起来。排查: journalctl -u $SVC -n 50 --no-pager" >&2
  exit 1
fi
echo "    /health ok（后端进程在线）"
echo "==> 就绪检查 http://127.0.0.1:$PORT/ready ："
if READY=$(curl -s -m 5 "http://127.0.0.1:$PORT/ready") && \
   [ "$(printf '%s' "$READY" | grep -c '"status":"ready"')" -ge 1 ]; then
  echo "    /ready ok（flyai CLI 与项目目录均就绪）"
else
  echo "!! /ready 未就绪——页面在线但旅行搜索不可用。返回: $READY" >&2
  echo "   flyai_cli=false             → dnf install -y nodejs npm && npm install -g @fly-ai/flyai-cli" >&2
  echo "   projects_dir_writable=false → 检查数据目录属主（chown -R 服务用户 $APP_DIR）" >&2
  exit 1
fi
```

（注意就绪探测的 curl 不带 `-f`：503 也要拿到 body 供 grep 判定；JSONResponse 输出无空格，grep 串 `"status":"ready"` 可匹配。）

- [ ] **Step 2: env.example 追加**

`deploy/aliyun/fieldwork-web.env.example` 的 `FLYAI_API_KEY=` 段（15 行）之后追加：

```bash
# ── 可选：应用访问令牌（审计 F02）──────────────────────────────────
# 部署在公网时强烈建议设置：非空后，所有 /projects、/settings 接口要求
# X-App-Token 请求头匹配（前端首页「API 密钥」卡填写同一令牌即可）。
# 留空或不设 = 不启用（本地开发默认）。
# FIELDWORK_APP_TOKEN=change-me-to-a-long-random-string
```

- [ ] **Step 3: DEPLOY.md 三处增补**

(a) 第 0 节（前置依赖）加三条：`curl`（健康检查）、`nodejs + npm + @fly-ai/flyai-cli`（旅行搜索；不装只影响 /ready 的 flyai_cli 项）、`FIELDWORK_APP_TOKEN` 公网令牌说明（指向 env.example 注释段）。

(b) 第 4 节（填写环境变量）密钥清单中加一行：`FIELDWORK_APP_TOKEN=`（公网部署建议设置，前端设置卡填同一令牌）。

(c) 第 9 节（排错表）加两行：

| 症状 | 原因与处置 |
| --- | --- |
| 接口 401 `app_token_required` | 服务端设了 FIELDWORK_APP_TOKEN 但浏览器未填/填错——首页「API 密钥」卡填写同一令牌 |
| `/ready` 返回 `not_ready` | 按响应里 checks 找 false 项：`flyai_cli=false` 装 CLI；`projects_dir_writable=false` 查目录属主 |

- [ ] **Step 4: 语法验证**

Run: `bash -n deploy/aliyun/deploy.sh`
Expected: 无输出（语法通过）。无 bash 的环境（纯 PowerShell）则人工审读三处修改的引号/反斜杠配对。

- [ ] **Step 5: 提交**

```bash
git add deploy/aliyun/deploy.sh deploy/aliyun/fieldwork-web.env.example deploy/aliyun/DEPLOY.md
git commit -m "fix(deploy): flyai CLI 依赖安装 + /ready fail-hard 健康检查（审计 F15）"
```

---

### Task 9: 全量回归

**Files:** 无新增——只跑校验。

- [ ] **Step 1: 后端 lint + 全量测试**

Run: `ruff check .`
Expected: 无告警（新增 `kernel/repository/guard.py`、`web_host/api/guard.py` 若触发未用导入等告警需修）

Run: `python -m pytest -q`
Expected: 全绿（既有用例零回归——特别注意 `tests/test_web_turns_api.py` 的 SSE 流式与新增中间件兼容）

- [ ] **Step 2: 前端全量**

Run: `cd web; npm test`
Expected: 全绿

- [ ] **Step 3: 修复后提交（若有）**

```bash
git add -A
git commit -m "chore: 审计第一批修复回归 lint 清理"
```

（lint 与测试全绿无改动时跳过本步。）

---

## Spec 验收对照

| 审计验收标准 | 对应任务与测试 |
| --- | --- |
| 恶意 ID 无越界副作用 | Task 1-3；`test_create_traversal_pid_rejected_no_side_effect` 断言目录未创建且项目列表为空 |
| 未授权不可读取/执行 | Task 4-5；中间件 401 `app_token_required`（含 CORS 预检兼容）+ 前端令牌注入 |
| 假凭证可验证逐回合传递 | Task 6；`test_run_command_injects_page_level_key` 断言子进程 env 携带页面 key |
| 全新目标机器按文档安装并区分「页面在线」「搜索可用」 | Task 7-8；`/health` vs `/ready` 语义分离，deploy.sh fail-hard 并给出修复指引 |
