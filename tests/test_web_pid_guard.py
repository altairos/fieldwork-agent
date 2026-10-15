# tests/test_web_pid_guard.py
"""web_host HTTP 入口的 project_id 守卫测试（终审 C1 的 HTTP 侧接入）。

三层覆盖：

1. helper 单元层——``checked_pid`` 对非法形状一律 ``HTTPException`` 400；
2. 路由层——穿越 id 在 POST /projects、GET /projects/{id}、proposal/turns 各入口
   被 400/404 拦下，且**无越界读写**（文件系统 + 响应体 + 假 repo 调用记录断言）；
3. 合法路径不回归——201/200 与"合法但不存在 → 404"语义不变（不得 400/500 化）。

全部走 tmp_path，不联网、不真调 flyai/DashScope（夹具写法同 test_web_projects_api.py）。

URL 形态说明（httpx 0.28 实测）：字面量 ``/projects/..`` 会被 httpx 点段归一成 ``/``
发出（404，路由不匹配）；``..%2F`` 形态解码后含 ``/``，单段路由 ``{project_id}``
不匹配 → 404（handler 未触达）；只有 ``%2E%2E``（解码后单段 ``..``）会进 handler
被守卫 400。断言按任务书写法：``status in (400, 404)`` 安全析取 + 独立的无越界读写
断言——不为了凑 400 放宽安全断言。
"""

import json

import pytest
from fastapi import HTTPException
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.api.guard import checked_pid
from fieldwork_agent.web_host.app import create_app
from fieldwork_agent.web_host.deps import init_deps
from httpx import ASGITransport, AsyncClient

# 与 tests/test_repository_guard.py 同一套非法形状（HTTP 侧一律折 400）
ILLEGAL_PIDS = [
    "", None, 42, "a" * 65, "../evil", "..\\..\\evil", ".", "./x", "a//b",
    "C:\\evil", "C:evil", "/etc/passwd", "p\x00id", "..",
]
ILLEGAL_IDS = ["空串", "None", "整数", "超长65", "穿越正斜杠", "穿越反斜杠", "点",
               "点斜杠", "空段", "盘符绝对", "盘符相对", "unix绝对", "null字节", "点点"]

MARKER = "TOPSECRET_MARKER_20261015"


# ---------------------------------------------------------------- helper 单元层


@pytest.mark.parametrize("pid", ILLEGAL_PIDS, ids=ILLEGAL_IDS)
def test_checked_pid_raises_400(pid):
    """非法形状 → HTTPException 400（消息含原因，不含文件系统路径）。"""
    with pytest.raises(HTTPException) as excinfo:
        checked_pid(pid)
    assert excinfo.value.status_code == 400
    assert "非法 project_id" in excinfo.value.detail


def test_checked_pid_passes_legal():
    """合法 id 原样返回（不误伤段内点号）。"""
    assert checked_pid("p-1_2") == "p-1_2"
    assert checked_pid("study..2024") == "study..2024"


# ---------------------------------------------------------------- 路由层夹具


@pytest.fixture
def app(tmp_path):
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


class RecordingRepo(JsonRepository):
    """记录调用的假 repo：断言守卫在**任何** repo 方法被触达之前就拦截。"""

    def __init__(self, projects_dir):
        super().__init__(projects_dir)
        self.calls: list[tuple[str, str]] = []

    def create(self, pid):
        self.calls.append(("create", pid))
        return super().create(pid)

    def get_state(self, pid):
        self.calls.append(("get_state", pid))
        return super().get_state(pid)

    def get_latest_proposal(self, pid):
        self.calls.append(("get_latest_proposal", pid))
        return super().get_latest_proposal(pid)

    def list_proposals(self, pid):
        self.calls.append(("list_proposals", pid))
        return super().list_proposals(pid)


@pytest.fixture
async def spy_client(tmp_path):
    repo = RecordingRepo(projects_dir=tmp_path / "projects")
    repo.create("p1")
    _app = create_app(config=None, repo=repo)
    init_deps(_app, config=None, repo=repo)
    repo.calls.clear()   # 清掉 setup 期调用，只看恶意请求触发的
    transport = ASGITransport(app=_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, repo


# ---------------------------------------------------------------- 路由层：写入口


@pytest.mark.parametrize("pid", ["../../evil_rel", "..\\..\\evil", "."])
async def test_post_projects_traversal_400_and_nothing_created(client, tmp_path, pid):
    """POST /projects 穿越 body：400，且 projects 根外无新目录、根内无 state.json。"""
    r = await client.post("/projects", json={"project_id": pid})
    assert r.status_code == 400
    assert "非法 project_id" in r.json()["detail"]
    # 400 消息不得泄露文件系统路径（只回显被拒 id 与原因）
    assert str(tmp_path) not in r.text
    # "../../evil_rel" 自 root=tmp_path/projects 若落地应在 tmp_path.parent/evil_rel
    assert not (tmp_path.parent / "evil_rel").exists()
    assert not list(tmp_path.rglob("*evil*"))
    # 根内也没有 state.json：守卫先于任何写入（"." 攻击的正是要把 state.json 写进根）
    projects_root = tmp_path / "projects"
    assert not (projects_root / "state.json").exists()


# ---------------------------------------------------------------- 路由层：读入口


@pytest.mark.parametrize("url", [
    "/projects/..%2F..%2Fsecret",   # 任务书指定形态：解码后含 /，单段路由不匹配 → 404
    "/projects/%2E%2E%2Fsecret",    # 全编码变体，同样路由不匹配 → 404
    "/projects/%2E%2E",             # 解码后单段 ".." → 触达 handler → 守卫 400
    "/projects/..",                 # httpx 点段归一为 "/" 发出 → 404
])
async def test_get_project_traversal_forms_blocked(client, tmp_path, url):
    """GET /projects/{id} 各穿越形态：400/404 均安全，且根外"秘密项目"未被读写。"""
    # 在 projects 根之外预置一个"秘密项目"（tmp_path/secret）：若穿越读到，标记会进响应体
    JsonRepository(projects_dir=tmp_path).create("secret")
    state_file = tmp_path / "secret" / "state.json"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data["research_context"] = {"topic": MARKER}
    state_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    before = state_file.read_bytes()

    r = await client.get(url)
    assert r.status_code in (400, 404)
    assert MARKER not in r.text                       # 无越界读
    assert state_file.read_bytes() == before          # 无越界写


@pytest.mark.parametrize(("method", "url"), [
    ("GET", "/projects/%2E%2E/proposal"),
    ("GET", "/projects/%2E%2E/proposal/download"),
    ("GET", "/projects/%2E%2E/proposals"),
    ("POST", "/projects/%2E%2E/messages"),
    ("GET", "/projects/%2E%2E/turns/abc123/events"),
    ("POST", "/projects/%2E%2E/turns/abc123/cancel"),
])
async def test_malicious_pid_never_reaches_repo(spy_client, method, url):
    """proposal/turns 全部入口：守卫先于 repo/TurnManager——假 repo 零调用记录。"""
    client, repo = spy_client
    if method == "GET":
        r = await client.get(url)
    else:
        r = await client.post(url, json={"text": "hi"})
    assert r.status_code in (400, 404)
    assert repo.calls == []


# ---------------------------------------------------------------- 合法路径不回归


async def test_legal_create_and_get_not_regressed(client):
    r = await client.post("/projects", json={"project_id": "p_ok", "topic": "宗族"})
    assert r.status_code == 201 and r.json()["project_id"] == "p_ok"
    r2 = await client.get("/projects/p_ok")
    assert r2.status_code == 200 and r2.json()["phase"] == "research_positioning"


async def test_dotted_legal_pid_roundtrip(client):
    """段内点号（study..2024）经 HTTP 全链路不误伤：create 201 + get 200。"""
    r = await client.post("/projects", json={"project_id": "study..2024"})
    assert r.status_code == 201
    r2 = await client.get("/projects/study..2024")
    assert r2.status_code == 200


async def test_auto_generated_id_still_legal(client):
    """POST /projects 缺省 id：自动生成 field_<时间戳> 统一过守卫后仍 201。"""
    r = await client.post("/projects", json={})
    assert r.status_code == 201
    assert r.json()["project_id"].startswith("field_")


async def test_legal_but_unknown_still_404(client):
    """格式合法但项目不存在 → 仍 404（不得把 404 变成 400，也不得 500）。"""
    assert (await client.get("/projects/ghost")).status_code == 404
    assert (await client.get("/projects/ghost/proposal")).status_code == 404
    r = await client.post("/projects/ghost/messages", json={"text": "hi"})
    assert r.status_code == 404
