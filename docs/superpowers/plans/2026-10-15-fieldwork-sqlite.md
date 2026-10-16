# 田野罗盘 SQLite 持久化实现计划（Plan 5 / 5）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 `SqliteRepository`（实现与 `JsonRepository` 完全一致的 `ProjectRepository` Protocol）+ `FA_STORAGE` 后端开关 + `fieldwork migrate --to-sqlite` 存量搬迁，落实 spec §5 里程碑 2（双实现并行）与里程碑 3（迁移 + JSON 只读回退）。

**Architecture:** Repository Protocol 是稳定边界，宿主只见 Protocol（spec §5.1）。SqliteRepository 用 stdlib `sqlite3`（无 ORM），9 张表 + `PRAGMA user_version` 管版本 + WAL + `busy_timeout=5000` 支持 MCP/Web 双进程并发读。每次操作开新连接（context manager），实例可随时重建、跨实例读到即库中真相——对齐 JsonRepository「无缓存、现读现写」哲学。记录类数据（田野点/报道人）以 JSON `data` 列存整条记录，保证与 Json 的 schemaless dict 逐字段平价（迁移「规范化 state 全等」的硬要求）。

**Tech Stack:** Python 3.10+ stdlib `sqlite3`（无第三方依赖）；pytest（L2 契约套件双跑）；现有 `core/cli.py`（prompt_toolkit）挂 `migrate` 子命令。

**Spec:** `docs/superpowers/specs/2026-10-14-fieldwork-agent-mcp-kernel-design.md` §5（持久化演进）+ §6.L2（Repository 契约）。本计划是 spec §5 的论证与落地。

## Global Constraints

以下为 spec 全局要求，**每个 Task 的需求隐含包含本节**（值逐字来自 spec / 现有代码）：

- **Protocol 逐条一致**：SqliteRepository 必须满足 `kernel/repository/protocol.py` 全部 23 方法语义，由 `tests/repository_contract.py`（30 用例）钉死；未知 `project_id`→`KeyError`，非法阶段→`ValueError`。
- **dict 进出**：Protocol 不绑 pydantic，宿主自行与 `kernel.models` 转换。
- **消息单轨制**：`messages` 表是全量唯一真源，`get_messages` 只读全量；`get_state["dialogue_context"]` 是 ≤50 条窗口副本（`MESSAGE_WINDOW=50`），不参与读取。
- **缓存 64KB 上限**：`cache_search` 序列化超 `CACHE_LIMIT_BYTES=64*1024` UTF-8 字节即把 `result` 整体替换为 `{"truncated": True, "preview": <按字节截断不产生半字符>}`。
- **幂等创建**：`create` 对已存在项目不清空存量。
- **记录 upsert**：田野点/报道人同 `id` 视为更新，无 `id` 则生成（时间戳前缀 + 随机后缀，全局唯一）。
- **全局唯一缓存 id**：`get_cached_search(cid)` 签名不含 pid，id 必须跨项目全局唯一。
- **提案版本累积**：`save_proposal` 重复保存不覆盖既有；记录含 `filename`（`.md` 结尾）+ `saved_at`；`content` 存 DB 为事实源（spec §5.3）。
- **无 ORM、stdlib sqlite3**：不引入 SQLAlchemy 等；`PRAGMA user_version` 管 schema 版本。
- **WAL + busy_timeout=5000**：支持 MCP Server / Web 宿主双进程并发读（spec §5.2）。
- **生长式迁移、每步 CI 绿**：JsonRepository 保持默认（`FA_STORAGE` 未设或非 `sqlite`）；SqliteRepository 并行可选；迁移后 JSON 目录原地保留（天然回滚）。
- **标识符英文、注释可中文**；Windows git 用 `& "C:\Program Files\Git\cmd\git.exe" -C f:\Agents_dev\Fieldwork_Agent ...`；Python 用 `.venv\Scripts\python.exe`；ruff line-length 100。

## 关键设计裁决（写计划时已定，执行者不得偏离）

1. **search_knowledge 用 LIKE 子串 AND，不用 FTS5 MATCH**（偏离 spec §5.2「FTS5 虚表」字面）。理由：契约要求大小写不敏感**子串** AND 匹配（`search_knowledge("集市 访谈")` 须命中 content「集市访谈材料」），而 FTS5 的 `unicode61` 分词器不切中文（「白族三月街」是单 token，MATCH '三月街' 不命中）、`trigram` 分词器要求 ≥3 字符（2 字 token「集市」「访谈」无法命中）。Protocol docstring 明写「FTS5 版本属里程碑 2，**接口不变**」——即行为必须与 Json 子串匹配一致。本地单人、知识条目量小，LIKE 子串足够快。**FTS5 全文检索延后为未来优化**（spec §7 已列向量化检索为后续）。messages 表不建 FTS5（无任何 Protocol 方法检索消息，YAGNI）。
2. **field_sites / informants 存整条记录 JSON `data` 列**（偏离 spec §5.2 的命名列 name/confirmed/profile、code/type/relation/...）。理由：JsonRepository 存 schemaless dict（任意字段 round-trip），且迁移验收要求「导入后规范化 state **全等**」——若用命名列 + NULL 填充，重建的 dict 会多出 `profile=None` 等键，与 Json 侧不等，破坏全等。故 `data` TEXT 存 `json.dumps(record)`，返回时 `json.loads(data)` 逐字段还原；命名列延后到有查询需求时再加（当前无 Protocol 方法按字段过滤）。
3. **每次操作开新连接**（`_connect()` context manager），不在实例上长持连接。理由：对齐 Json「实例可随时重建、无缓存」；规避 sqlite3 跨线程（web_host async + `asyncio.to_thread`）的 `check_same_thread` 问题；本地单人性能足够。schema 在 `__init__` 确保一次。
4. **proposals 表含 `filename` 列**（合成导出名 `{kind}_{version}_{ts}.md`）。理由：契约 `test_proposal_record_fields` 断言 `latest["filename"].endswith(".md")`；spec §5.3「content 存 DB，按需导出 .md」——filename 是导出名，DB 内 content 为事实源。
5. **迁移校验 = 行数 + 抽样字段哈希 + 规范化核心 state 全等**（spec §5.4/§6.L2）。「规范化 state」指迁移的 7 类数据（projects/phase、research_context、messages、field_sites、informants、search_cache、knowledge、proposals）；Json 侧 vestigial 旧单值键（travel_plan/researcher_info/search_results/notes）不迁移，sqlite.get_state 以空默认值补齐 shape（宿主读这些键不报错）。

## File Structure

```
kernel/repository/
├── protocol.py          （不改）ProjectRepository Protocol — 稳定边界
├── json_repo.py         （不改）JsonRepository — 里程碑1默认实现，parity 参照源
├── sqlite_repo.py       （新建）SqliteRepository — 里程碑2，同一 Protocol
├── factory.py           （新建）make_repository(config) — FA_STORAGE 开关
└── __init__.py          （改）导出 SqliteRepository / make_repository
core/
├── config.py            （改）加 storage_backend(FA_STORAGE) + sqlite_path
├── cli.py               （改）加 migrate 子命令
└── migrate.py           （新建）JSON→SQLite 搬迁逻辑（可独立测试，CLI 薄壳调用）
web_host/deps.py         （改）init_deps 用 make_repository 替代直接 JsonRepository
mcp_server/server.py     （改）repo=None 分支用 make_repository
tests/
├── repository_contract.py   （改1处）test_cache_truncated_64kb 中立化 + REPO_FACTORIES 加 make_sqlite_repo
├── test_sqlite_repo.py      （新建）sqlite 实现细节单测（schema/WAL/持久/各方法组）
├── test_repo_factory.py     （新建）FA_STORAGE 开关
└── test_migrate.py          （新建）迁移 fixture（legacy_project → sqlite）+ 幂等 + 校验
```

**Parity 参照源**：实现每个方法时，逐条对照 `kernel/repository/json_repo.py` 的对应方法行为（返回 dict 的键、顺序、边界）。契约套件 `tests/repository_contract.py` 是最终平价门禁。

## SQLite Schema（DDL，`PRAGMA user_version=1`）

```sql
PRAGMA journal_mode=WAL;          -- 连接后执行（非 DDL）
PRAGMA busy_timeout=5000;         -- 连接后执行
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS projects (
    project_id    TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    phase         TEXT NOT NULL,
    phase_history TEXT NOT NULL DEFAULT '[]'   -- JSON array
);
CREATE TABLE IF NOT EXISTS research_context (
    project_id TEXT PRIMARY KEY,
    data       TEXT NOT NULL DEFAULT '{}',      -- JSON object
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}',      -- JSON；返回时键名为 metadata
    ts         TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_project ON messages(project_id, id);

CREATE TABLE IF NOT EXISTS field_sites (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    saved_at   TEXT NOT NULL,
    seq        INTEGER NOT NULL,                -- 保存序（list 稳定排序）
    data       TEXT NOT NULL,                   -- JSON 整条记录（裁决2）
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sites_project ON field_sites(project_id, seq);

CREATE TABLE IF NOT EXISTS informants (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    saved_at   TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    data       TEXT NOT NULL,                   -- JSON 整条记录
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_informants_project ON informants(project_id, seq);

CREATE TABLE IF NOT EXISTS search_cache (
    id         TEXT PRIMARY KEY,                -- 全局唯一（裁决：cid 不带 pid）
    project_id TEXT NOT NULL,
    query      TEXT NOT NULL,
    tool       TEXT NOT NULL,
    result     TEXT NOT NULL,                   -- JSON；写入前已应用 64KB 截断
    cached_at  TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cache_project ON search_cache(project_id, cached_at);

CREATE TABLE IF NOT EXISTS knowledge (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'user',
    category   TEXT NOT NULL DEFAULT 'general',
    tags       TEXT NOT NULL DEFAULT '[]',      -- JSON array
    created_at TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_project ON knowledge(project_id, seq);

CREATE TABLE IF NOT EXISTS proposals (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind       TEXT NOT NULL,
    version    TEXT NOT NULL,
    filename   TEXT NOT NULL,                   -- 合成导出名（裁决4）
    content    TEXT NOT NULL,                   -- 事实源（spec §5.3）
    saved_at   TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_proposals_project ON proposals(project_id, seq);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
```

`seq` 列用一个每项目递增计数（或全局 `MAX(seq)+1`）保证 list 按保存序返回（Json 侧靠文件 `sorted()` glob + 列表追加序）。`created_at` 列名在 knowledge/proposals 内部，但返回 dict 的键须与 Json 一致（knowledge 用 `created_at`，proposals 记录用 `saved_at`）。

---

### Task 1: SQLite 基建 + schema + 连接管理

**Files:**
- Create: `kernel/repository/sqlite_repo.py`
- Test: `tests/test_sqlite_repo.py`

**Interfaces:**
- Produces: `SqliteRepository(db_path: Path | str)`；`.db_path` 属性；`._connect()` context manager（yield `sqlite3.Connection`，row_factory=`sqlite3.Row`，已设 WAL/busy_timeout/foreign_keys）；`._ensure_schema()`（建表 + `PRAGMA user_version=1`）；`._require(pid)`（未知项目→`KeyError`）。后续 Task 逐组加 Protocol 方法。

- [ ] **Step 1: 写失败测试** `tests/test_sqlite_repo.py`

```python
"""SqliteRepository 实现细节单测（契约平价由 repository_contract 双跑把关，这里测 sqlite 专属）。"""
import sqlite3
from pathlib import Path

import pytest
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository


def make_sqlite_repo(tmp_path: Path) -> SqliteRepository:
    return SqliteRepository(db_path=tmp_path / "fieldwork.db")


def test_schema_created_with_user_version(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    with sqlite3.connect(repo.db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert version == 1
    for t in ("projects", "research_context", "messages", "field_sites",
              "informants", "search_cache", "knowledge", "proposals", "schema_meta"):
        assert t in tables, f"缺表 {t}"


def test_wal_mode_enabled(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_unknown_project_raises_keyerror(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    with pytest.raises(KeyError):
        repo._require("nope")
```

（本 Task 只写这三条 schema/WAL/KeyError 测试，均不依赖未实现的方法。`test_instance_recreatable_same_db`（验证跨实例持久）依赖 `create`/`get_phase`，**放到 Task 2** 写。）

- [ ] **Step 2: 运行确认失败** — `.venv\Scripts\python.exe -m pytest tests/test_sqlite_repo.py -q`（模块不存在）

- [ ] **Step 3: 实现基建** `kernel/repository/sqlite_repo.py`

```python
"""``SqliteRepository`` — ``ProjectRepository`` 的 SQLite 实现（spec §5 里程碑 2）。

与 JsonRepository 逐行为对齐（由 tests/repository_contract.py 双跑钉死）。要点：
- stdlib sqlite3、无 ORM；PRAGMA user_version 管 schema 版本；
- WAL + busy_timeout=5000 支持 MCP/Web 双进程并发读；
- 每次操作开新连接（context manager），实例可随时重建、无缓存；
- 记录类（田野点/报道人）以 JSON data 列存整条记录，保证 schemaless dict 平价。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from fieldwork_agent.kernel.guidance.phases import PHASES

SCHEMA_VERSION = 1
MESSAGE_WINDOW = 50
CACHE_LIMIT_BYTES = 64 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    ... 复制上方「SQLite Schema（DDL）」一节的全部 CREATE TABLE / CREATE INDEX 语句 ...
    （不含 PRAGMA journal_mode / busy_timeout / foreign_keys——那三条是连接级设置，在 _connect() 里执行）
"""


def _now() -> str:
    return datetime.now().isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}"


class SqliteRepository:
    """以单文件 SQLite 库为后端的持久化实现（FA_STORAGE=sqlite 时启用）。"""

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            cur = conn.execute("PRAGMA user_version").fetchone()[0]
            if cur == 0:
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            # cur < SCHEMA_VERSION 的升级迁移留待未来版本（当前 v1）

    def _require(self, pid: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM projects WHERE project_id=?", (pid,)).fetchone()
        if row is None:
            raise KeyError(f"未知项目: {pid}")

    def _next_seq(self, conn, table: str, pid: str) -> int:
        row = conn.execute(
            f"SELECT COALESCE(MAX(seq),0)+1 FROM {table} WHERE project_id=?", (pid,)).fetchone()
        return int(row[0])
```

- [ ] **Step 4: 运行确认通过** — `pytest tests/test_sqlite_repo.py -q`（schema/WAL/KeyError 三条绿）；`ruff check kernel/repository/sqlite_repo.py tests/test_sqlite_repo.py`

- [ ] **Step 5: Commit** — `& "C:\Program Files\Git\cmd\git.exe" -C f:\Agents_dev\Fieldwork_Agent add kernel/repository/sqlite_repo.py tests/test_sqlite_repo.py`；`git commit -m "feat(repo): SqliteRepository基建(schema/WAL/user_version/连接管理)"`

---

### Task 2: 项目面 + 阶段 + 研究上下文

**Files:** Modify `kernel/repository/sqlite_repo.py`；Test `tests/test_sqlite_repo.py`

**Interfaces:**
- Consumes: Task 1 的 `_connect/_ensure_schema/_require/_now`
- Produces: `create(pid)`（幂等）、`list_projects()->list[dict]`（按 project_id 排序，键 project_id/phase/created_at/updated_at）、`get_state(pid)->dict`（副本，含全部 shape 键）、`get_phase(pid)->str`、`set_phase(pid,phase)`（ValueError/同阶段不写历史/转移记 phase_history）、`update_research_context(pid,patch)`（浅合并）、`get_research_context(pid)->dict`（副本）

**get_state 返回 shape（对齐 json_repo `_default_state` + 契约 test_state_shape）：** `project_id, created_at, updated_at, phase, phase_history, research_context, dialogue_context(≤50窗口), field_sites, informants, proposals(记录不含content), travel_plan=None, researcher_info={}, proposal_draft=None, final_proposal=None, search_results=[], notes=[]`。每次返回全新 dict（副本语义）。

- [ ] **Step 1: 写失败测试**（追加到 test_sqlite_repo.py）

```python
def test_create_idempotent_preserves(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1"); repo.set_phase("p1", "field_site_profiles")
    repo.create("p1")   # 重复 create = 打开存量，不清空
    assert repo.get_phase("p1") == "field_site_profiles"


def test_list_projects_sorted_with_summary(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p2"); repo.create("p1")
    projects = repo.list_projects()
    assert [p["project_id"] for p in projects] == ["p1", "p2"]
    assert projects[0]["phase"] == "research_positioning"
    assert projects[0]["created_at"] and projects[0]["updated_at"]


def test_set_phase_history_and_errors(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    with pytest.raises(ValueError):
        repo.set_phase("p1", "no_such_phase")
    repo.set_phase("p1", "field_site_profiles")
    repo.set_phase("p1", "field_site_profiles")   # 同阶段不写历史
    hist = repo.get_state("p1")["phase_history"]
    assert len(hist) == 1
    assert hist[-1] == {"from": "research_positioning", "to": "field_site_profiles", "at": hist[-1]["at"]}


def test_research_context_shallow_merge(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    repo.update_research_context("p1", {"discipline": "人类学", "topic": "宗族"})
    repo.update_research_context("p1", {"topic": "宗族组织变迁"})
    ctx = repo.get_research_context("p1")
    assert ctx == {"discipline": "人类学", "topic": "宗族组织变迁"}


def test_get_state_returns_copy(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    st = repo.get_state("p1"); st["phase"] = "hacked"
    assert repo.get_state("p1")["phase"] == "research_positioning"


def test_get_state_shape_keys(tmp_path):
    repo = make_sqlite_repo(tmp_path)
    repo.create("p1")
    st = repo.get_state("p1")
    for k in ("project_id", "created_at", "updated_at", "phase", "phase_history",
              "research_context", "dialogue_context", "field_sites", "informants", "proposals"):
        assert k in st, f"缺键 {k}"


def test_instance_recreatable_same_db(tmp_path):
    """实例可随时重建（无缓存），跨实例读到即库中真相。"""
    a = make_sqlite_repo(tmp_path)
    a.create("p1")
    b = make_sqlite_repo(tmp_path)   # 新实例指同一 db 文件
    assert b.get_phase("p1") == "research_positioning"
```

- [ ] **Step 2: 确认失败** → **Step 3: 实现**（对照 json_repo.py L196-248 逐行为；`phase_history`/`research_context` 以 JSON 列存取；`create` 幂等：`INSERT OR IGNORE` 建 projects 行 + research_context 行）→ **Step 4: 通过**（本 Task 全部测试转绿，含跨实例持久的 `test_instance_recreatable_same_db`）

`create` 实现要点：
```python
def create(self, project_id: str) -> None:
    now = _now()
    with self._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO projects(project_id,created_at,updated_at,phase,phase_history)"
            " VALUES(?,?,?,?,?)",
            (project_id, now, now, PHASES[0], "[]"))
        conn.execute(
            "INSERT OR IGNORE INTO research_context(project_id,data) VALUES(?,?)",
            (project_id, "{}"))
```
`set_phase` 要点：读当前 phase → 相同则 return（不写）→ 否则 append `{from,to,at}` 到 phase_history JSON、更新 phase + updated_at。`get_state` 要点：`_require` → 读 projects 行 + research_context.data + 最近 ≤50 messages（dialogue_context）+ field_sites/informants/proposals 记录 → 组装含 legacy 空默认的完整 dict → 返回。

- [ ] **Step 5: Commit** — `git commit -m "feat(repo): SqliteRepository项目面/阶段/研究上下文"`

---

### Task 3: 消息史（单轨 + 窗口）

**Files:** Modify `sqlite_repo.py`；Test `test_sqlite_repo.py`

**Interfaces:**
- Produces: `append_message(pid, role, content, meta=None)`（写 messages 表全量；返回 dict 键为 `metadata`）、`get_messages(pid, limit=30)->list[dict]`（只读全量，limit≤0 取全部，返回 tail，每条含 role/content/ts/metadata）
- get_state 的 `dialogue_context` = 最近 ≤50 条（本 Task 补齐 Task 2 的窗口拼装）

- [ ] **Step 1: 写失败测试**

```python
def test_messages_full_history_no_loss(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    for i in range(60):
        repo.append_message("p1", "user", f"m{i}")
    assert len(repo.get_messages("p1", limit=100)) == 60


def test_messages_fields_and_order(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    repo.append_message("p1", "user", "提问", meta={"tool": "search_ai"})
    repo.append_message("p1", "assistant", "回答")
    msgs = repo.get_messages("p1", limit=2)
    assert [m["content"] for m in msgs] == ["提问", "回答"]
    assert msgs[0]["role"] == "user"
    assert msgs[0]["metadata"] == {"tool": "search_ai"}
    assert msgs[1]["metadata"] == {}
    assert all(m["ts"] for m in msgs)


def test_messages_limit_returns_tail(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    for i in range(10):
        repo.append_message("p1", "user", f"m{i}")
    assert [m["content"] for m in repo.get_messages("p1", limit=3)] == ["m7", "m8", "m9"]


def test_dialogue_context_window_capped_50(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    for i in range(60):
        repo.append_message("p1", "user", f"m{i}")
    assert len(repo.get_state("p1")["dialogue_context"]) == 50   # 窗口副本 ≤50
    assert len(repo.get_messages("p1", limit=0)) == 60           # 全量真源不丢
```

- [ ] **Step 2-4:** 实现（`append_message` INSERT 一行，meta→`json.dumps`，ts=`_now()`；`get_messages` `SELECT ... ORDER BY id` 取全量后 `[-limit:]`，meta→`metadata` 键；dialogue_context = `get_messages(pid, MESSAGE_WINDOW)`）；确认通过。
- [ ] **Step 5: Commit** — `git commit -m "feat(repo): SqliteRepository消息单轨史+50条窗口"`

---

### Task 4: 田野点 + 报道人（JSON data upsert）

**Files:** Modify `sqlite_repo.py`；Test `test_sqlite_repo.py`

**Interfaces:**
- Produces: `save_field_site(pid, record)->str`、`list_field_sites(pid)->list[dict]`、`save_informant(pid, record)->str`、`list_informants(pid)->list[dict]`。upsert：record 带 `id` 则更新（保留原 seq），否则 `_new_id("site"/"inf")` 新建（seq=next）；写入 `saved_at`；返回 record 的 `id`。list 按 seq 升序，返回 `json.loads(data)`（含 id/saved_at + 任意原始字段）。

**裁决2 落地**：`data` 列存 `json.dumps(entry)`（entry = 原 record + id + saved_at），返回时 `json.loads` 逐字段还原——保证与 Json schemaless dict 全等（迁移验收硬要求）。

- [ ] **Step 1: 写失败测试**

```python
def test_site_informant_roundtrip(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    repo.save_field_site("p1", {"name": "喜洲", "confirmed": True})
    repo.save_informant("p1", {"code": "P1", "type": "长老"})
    assert repo.list_field_sites("p1")[0]["confirmed"] is True
    assert repo.list_informants("p1")[0]["type"] == "长老"


def test_record_without_id_gets_one(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    sid = repo.save_field_site("p1", {"name": "大理"})
    iid = repo.save_informant("p1", {"code": "P2"})
    assert sid and iid
    assert repo.list_field_sites("p1")[0]["id"] == sid
    assert repo.list_informants("p1")[0]["id"] == iid


def test_same_id_updates_in_place(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    sid = repo.save_field_site("p1", {"name": "喜洲", "confirmed": False})
    repo.save_field_site("p1", {"id": sid, "name": "喜洲", "confirmed": True})
    sites = repo.list_field_sites("p1")
    assert len(sites) == 1 and sites[0]["confirmed"] is True


def test_arbitrary_fields_roundtrip(tmp_path):
    """schemaless：任意额外字段必须原样 round-trip（迁移全等的前提）。"""
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    repo.save_field_site("p1", {"name": "X", "custom": {"nested": [1, 2]}, "confirmed": False})
    got = repo.list_field_sites("p1")[0]
    assert got["custom"] == {"nested": [1, 2]}
    assert "profile" not in got   # 不得凭空填充未提供的键（与 Json 平价）


def test_save_order_preserved(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    for n in ("a", "b", "c"):
        repo.save_field_site("p1", {"name": n})
    assert [s["name"] for s in repo.list_field_sites("p1")] == ["a", "b", "c"]
```

- [ ] **Step 2-4:** 实现（`save_*`：entry=dict(record)，entry["id"]=record.get("id") or _new_id(prefix)，entry["saved_at"]=_now()；带 id 且已存在→UPDATE data+saved_at 保留 seq，否则 INSERT seq=next；`list_*`：`SELECT data FROM ... ORDER BY seq` → `[json.loads(r["data"]) for r in rows]`）；确认通过。
- [ ] **Step 5: Commit** — `git commit -m "feat(repo): SqliteRepository田野点/报道人(JSON data upsert+保存序)"`

---

### Task 5: 搜索缓存（64KB 截断 + 全局 id）

**Files:** Modify `sqlite_repo.py`；Test `test_sqlite_repo.py`

**Interfaces:**
- Produces: `cache_search(pid, query, tool, result)->str`（cid 全局唯一；序列化超 64KB → result 替换为 `{"truncated":True,"preview":<字节截断>}`）、`list_cached_searches(pid)->list[dict]`（索引 `{id,query,tool,cached_at}` 不含 result）、`get_cached_search(cid)->dict|None`（按全局 cid 跨项目定位，含 result；不存在→None）

- [ ] **Step 1: 写失败测试**

```python
def test_cache_roundtrip(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    cid = repo.cache_search("p1", "大理住宿", "search_hotel", {"items": [1, 2]})
    full = repo.get_cached_search(cid)
    assert full["query"] == "大理住宿" and full["tool"] == "search_hotel"
    assert full["result"] == {"items": [1, 2]}
    assert [c["id"] for c in repo.list_cached_searches("p1")] == [cid]
    assert "result" not in repo.list_cached_searches("p1")[0]   # 索引不含 result


def test_unknown_cache_id_returns_none(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    assert repo.get_cached_search("no_such_id") is None


def test_cache_truncated_64kb(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    cid = repo.cache_search("p1", "q", "search_ai", {"blob": "x" * 100_000})
    stored = repo.get_cached_search(cid)["result"]
    assert stored["truncated"] is True
    assert len(stored["preview"].encode("utf-8")) <= 64 * 1024


def test_get_cached_search_cross_project(tmp_path):
    """cid 全局唯一：不带 pid 也能跨项目定位。"""
    repo = make_sqlite_repo(tmp_path); repo.create("p1"); repo.create("p2")
    cid = repo.cache_search("p2", "q", "search_ai", {"x": 1})
    assert repo.get_cached_search(cid)["result"] == {"x": 1}
```

- [ ] **Step 2-4:** 实现（对照 json_repo.py L291-341：截断逻辑逐字一致——`serialized.encode("utf-8")[:CACHE_LIMIT_BYTES].decode("utf-8", errors="ignore")`；`get_cached_search` 用 `SELECT ... WHERE id=?`（id 全局唯一 PK，天然跨项目））；确认通过。
- [ ] **Step 5: Commit** — `git commit -m "feat(repo): SqliteRepository搜索缓存(64KB截断+全局id跨项目)"`

---

### Task 6: 知识库（LIKE 子串 AND 检索 — 裁决1）

**Files:** Modify `sqlite_repo.py`；Test `test_sqlite_repo.py`

**Interfaces:**
- Produces: `add_knowledge(pid, title, content, source="user", tags=None, category="general")->str`、`list_knowledge(pid)->list[dict]`（按 seq 升序，含正文）、`search_knowledge(pid, text)->list[dict]`（大小写不敏感**子串** AND：空白分词，title+content+tags 拼串须含全部 token；空 query→全部）、`delete_knowledge(pid, entry_id)`（不存在不报错，幂等）

**裁决1 落地**：`search_knowledge` 用 `LIKE '%token%'`（或取回后 Python 侧子串过滤）实现与 Json 逐字一致的子串 AND 语义，**不用 FTS5 MATCH**（FTS5 无法命中短中文 token）。知识条目返回 dict 键：`id,title,content,source,tags,category,created_at`（对齐 json_repo L355-363）。

- [ ] **Step 1: 写失败测试**

```python
def test_knowledge_roundtrip_and_search(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    kid = repo.add_knowledge("p1", "白族三月街", "集市访谈材料", tags=["田野", "集市"])
    entries = repo.list_knowledge("p1")
    assert [e["id"] for e in entries] == [kid]
    assert entries[0]["source"] == "user" and entries[0]["category"] == "general"
    assert entries[0]["tags"] == ["田野", "集市"]
    assert [e["id"] for e in repo.search_knowledge("p1", "三月街")] == [kid]   # 子串
    assert repo.search_knowledge("p1", "集市 访谈") == entries                # 2字token AND
    assert repo.search_knowledge("p1", "不存在") == []


def test_knowledge_search_case_insensitive(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    repo.add_knowledge("p1", "Field Notes", "Ethnography")
    assert len(repo.search_knowledge("p1", "field")) == 1     # 大小写不敏感
    assert len(repo.search_knowledge("p1", "ETHNO")) == 1


def test_delete_knowledge_idempotent(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    kid = repo.add_knowledge("p1", "提纲", "问题1", category="methods")
    repo.delete_knowledge("p1", kid)
    repo.delete_knowledge("p1", kid)   # 再删不报错
    assert repo.list_knowledge("p1") == []
```

- [ ] **Step 2-4:** 实现（对照 json_repo.py L345-396：`add_knowledge` INSERT 带 seq；`search_knowledge` 取 `list_knowledge` 后按 token 子串 AND 过滤——`haystack = " ".join([title, content, " ".join(tags)]).lower()`，`all(t in haystack for t in tokens)`，与 Json 逐字一致）；确认通过。
- [ ] **Step 5: Commit** — `git commit -m "feat(repo): SqliteRepository知识库(LIKE子串AND,契约平价非FTS5)"`

---

### Task 7: 提案文档（版本累积 + content inline + filename）

**Files:** Modify `sqlite_repo.py`；Test `test_sqlite_repo.py`

**Interfaces:**
- Produces: `save_proposal(pid, markdown, kind="proposal", version="draft")->str`（不覆盖既有，版本累积；生成 `filename={kind}_{version}_{ts}.md` + `saved_at`；content 存 DB）、`get_latest_proposal(pid)->dict|None`（最近保存，含 content）、`list_proposals(pid)->list[dict]`（按 saved_at 升序，每条含 id/kind/version/filename/saved_at/content）

**裁决4 落地**：proposals 表含 `filename` 列（合成导出名）；`content` 列是事实源。get_state 的 `proposals` 键返回**不含 content** 的记录（对齐 Json：state["proposals"] 是登记记录，content 由 list/latest 补）。

- [ ] **Step 1: 写失败测试**

```python
def test_proposal_versions_accumulate(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    repo.save_proposal("p1", "v1", version="draft")
    repo.save_proposal("p1", "v2", version="draft")
    assert len(repo.list_proposals("p1")) == 2   # 不覆盖
    assert repo.get_latest_proposal("p1")["content"] == "v2"


def test_proposal_record_fields(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    pid = repo.save_proposal("p1", "# 研究方案", kind="proposal", version="final")
    latest = repo.get_latest_proposal("p1")
    assert latest["id"] == pid
    assert latest["kind"] == "proposal" and latest["version"] == "final"
    assert latest["filename"].endswith(".md")
    assert latest["saved_at"]
    assert latest["content"] == "# 研究方案"


def test_no_proposal_returns_none(tmp_path):
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    assert repo.get_latest_proposal("p1") is None
    assert repo.list_proposals("p1") == []


def test_get_state_proposals_exclude_content(tmp_path):
    """get_state 的 proposals 是登记记录（不含 content），对齐 Json。"""
    repo = make_sqlite_repo(tmp_path); repo.create("p1")
    repo.save_proposal("p1", "正文", version="draft")
    st = repo.get_state("p1")
    assert len(st["proposals"]) == 1
    assert "content" not in st["proposals"][0]
    assert st["proposals"][0]["filename"].endswith(".md")
```

- [ ] **Step 2-4:** 实现（对照 json_repo.py L400-453：`save_proposal` INSERT 带 seq/filename/saved_at/content；`list_proposals` `SELECT ... ORDER BY seq`（或 saved_at,id）→ 每条含 content；`get_latest_proposal` = list 末条；get_state 的 proposals 用 `SELECT id,kind,version,filename,saved_at`（不取 content））；确认通过。
- [ ] **Step 5: Commit** — `git commit -m "feat(repo): SqliteRepository提案文档(版本累积+content inline+filename)"`

---

### Task 8: 契约套件中立化 + SqliteRepository 双跑门禁（里程碑 2 验收）

**Files:** Modify `tests/repository_contract.py`（仅 2 处）、`kernel/repository/__init__.py`；Test 复用契约套件

**Interfaces:**
- Produces: `repository_contract.REPO_FACTORIES` 含 `make_sqlite_repo`；30 用例 Json/Sqlite 双跑全绿

**关键前置**：契约里 `test_cache_truncated_64kb`（L142-150）直读 Json 文件布局 `repo.root/p1/search_cache/{cid}.json`——对 SQLite 不成立。必须先把它**中立化为 Protocol-based**（经 `get_cached_search` 断言截断），才能实现 docstring 承诺的「加 make_sqlite_repo 进 REPO_FACTORIES 即双跑」。

- [ ] **Step 1: 中立化截断用例**（改 `tests/repository_contract.py` L142-150）

把直读文件改为经 Protocol：
```python
    def test_cache_truncated_64kb(self, repo):
        big = {"blob": "x" * 100_000}
        cid = repo.cache_search("p1", "q", "search_ai", big)
        stored = repo.get_cached_search(cid)["result"]   # 经 Protocol，不直读落盘
        assert stored["truncated"] is True
        assert len(stored["preview"].encode("utf-8")) <= 64 * 1024
```
同步删除文件顶部 `import json`（若仅此用例使用）与 docstring 里「借用 root 属性」的例外说明（改为「全部用例仅经 Protocol」）。`root` 属性仍保留在 JsonRepository（宿主排障用），但契约不再依赖它。

- [ ] **Step 2: 加 sqlite 工厂进 REPO_FACTORIES**（改 `repository_contract.py` L20-24）

```python
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository


def make_json_repo(tmp_path):
    return JsonRepository(projects_dir=tmp_path / "projects")


def make_sqlite_repo(tmp_path):
    return SqliteRepository(db_path=tmp_path / "fieldwork.db")


REPO_FACTORIES = [make_json_repo, make_sqlite_repo]
```

- [ ] **Step 3: 导出** `kernel/repository/__init__.py` 加 `SqliteRepository`（与 `make_repository` 一并，Task 9 补 factory 导出）。

- [ ] **Step 4: 双跑全绿** — `.venv\Scripts\python.exe -m pytest tests/repository_contract.py tests/test_json_repo.py tests/test_sqlite_repo.py -q`。此时 30 契约用例 ×2 实现全过 = 里程碑 2 平价达成。若某用例 sqlite 侧红，回对应 Task 修实现（**不改契约断言迁就实现**）。

- [ ] **Step 5: Commit** — `git commit -m "test(repo): 契约套件中立化+SqliteRepository双跑门禁(里程碑2平价)"`

---

### Task 9: FA_STORAGE 工厂 + 宿主接线

**Files:** Create `kernel/repository/factory.py`；Modify `core/config.py`、`web_host/deps.py`、`mcp_server/server.py`、`kernel/repository/__init__.py`；Test `tests/test_repo_factory.py`

**Interfaces:**
- Produces: `Config.storage_backend`（`os.getenv("FA_STORAGE","json")`）、`Config.sqlite_path`（`CONFIG_DIR/"fieldwork.db"`）；`make_repository(config)->ProjectRepository`（`storage_backend=="sqlite"`→SqliteRepository(config.sqlite_path)，否则 JsonRepository(config.projects_dir)）
- 宿主接线：`web_host/deps.py` 与 `mcp_server/server.py` 的 repo 构造改用 `make_repository(cfg)`

**注意**：`web_host/deps.py` 的 `TurnManager(root=cfg.projects_dir)` 是 R4 回合事件持久化（独立于 ProjectRepository，spec §5.2 schema 不含 turns/events 表），**本 Task 不动 TurnManager**——它继续用 JSON 文件（spec §3.3：事件层 SQLite 仅在「JSON 扛不住」时触发，属未来条件）。

- [ ] **Step 1: 写失败测试** `tests/test_repo_factory.py`

```python
import pytest
from fieldwork_agent.kernel.repository.factory import make_repository
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository


class _Cfg:
    def __init__(self, tmp_path, backend):
        self.storage_backend = backend
        self.projects_dir = tmp_path / "projects"
        self.sqlite_path = tmp_path / "fieldwork.db"


def test_default_is_json(tmp_path):
    repo = make_repository(_Cfg(tmp_path, "json"))
    assert isinstance(repo, JsonRepository)


def test_sqlite_switch(tmp_path):
    repo = make_repository(_Cfg(tmp_path, "sqlite"))
    assert isinstance(repo, SqliteRepository)


def test_unknown_backend_falls_back_json(tmp_path):
    repo = make_repository(_Cfg(tmp_path, "weird"))
    assert isinstance(repo, JsonRepository)


def test_sqlite_backend_functional(tmp_path):
    repo = make_repository(_Cfg(tmp_path, "sqlite"))
    repo.create("p1"); repo.set_phase("p1", "field_site_profiles")
    assert make_repository(_Cfg(tmp_path, "sqlite")).get_phase("p1") == "field_site_profiles"
```

- [ ] **Step 2: 确认失败** → **Step 3: 实现**

`core/config.py`（在 `__init__` 模型分档附近加）：
```python
        # 存储后端（spec §5.1 里程碑2：FA_STORAGE=sqlite 切换，默认 json）
        self.storage_backend: str = os.getenv("FA_STORAGE", "json").lower()
        self.sqlite_path: Path = CONFIG_DIR / "fieldwork.db"
```
`kernel/repository/factory.py`：
```python
"""存储后端工厂：按 FA_STORAGE 选 Json/Sqlite 实现（spec §5.1 里程碑2 双实现并行）。"""
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository


def make_repository(config):
    """config.storage_backend=='sqlite' → SqliteRepository；否则 JsonRepository（默认/未知值）。"""
    if getattr(config, "storage_backend", "json") == "sqlite":
        return SqliteRepository(db_path=config.sqlite_path)
    return JsonRepository(projects_dir=config.projects_dir)
```
`web_host/deps.py` L8/L14：`from ...factory import make_repository`；`repo = make_repository(cfg)`（替换直接 JsonRepository）。`mcp_server/server.py` L67/L69 同样替换。`kernel/repository/__init__.py` 导出 `make_repository`。

- [ ] **Step 4: 通过** — `pytest tests/test_repo_factory.py -q` + 回归 `pytest tests/test_web_projects_api.py tests/test_mcp_server_contract.py -q`（宿主默认 json 后端不受影响）+ `ruff check`。

- [ ] **Step 5: Commit** — `git commit -m "feat(repo): FA_STORAGE后端工厂+Web/MCP宿主接线(默认json不变)"`

---

### Task 10: fieldwork migrate --to-sqlite（里程碑 3）+ 文档

**Files:** Create `core/migrate.py`；Modify `core/cli.py`、`README.md`、`DEVELOPMENT.md`；Test `tests/test_migrate.py`

**Interfaces:**
- Consumes: `JsonRepository`（读存量）、`SqliteRepository`（写）、`make_repository`
- Produces: `migrate_to_sqlite(json_repo, sqlite_repo, project_id=None)->dict`（扫描存量项目 → 逐项目导入 7 类数据 → 行数 + 抽样字段哈希校验 → `schema_meta` 写 `migrated_from` → 幂等）；CLI `fieldwork migrate --to-sqlite [--project-id X]`

**迁移 7 类数据**（spec §5.4）：projects/phase（含 phase_history）、research_context、messages（全量 jsonl）、field_sites、informants、search_cache、knowledge、proposals（含旧单值键 proposal_draft/final_proposal → 并入 proposals）。JSON 目录**原地保留**（天然回滚）。

**幂等**：已迁移项目（sqlite 已有该 project_id 且 schema_meta 标记）再次 migrate 不产生重复行——用 `INSERT OR REPLACE` / 先查存在。校验：每类数据迁移后行数 == Json 侧计数；抽样字段（如首条 message content、首个 site name）哈希一致。

- [ ] **Step 1: 写失败测试** `tests/test_migrate.py`（用 `tests/fixtures/legacy_project/`）

```python
import shutil
from pathlib import Path

import pytest
from fieldwork_agent.core.migrate import migrate_to_sqlite
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_project"


@pytest.fixture
def legacy_json_repo(tmp_path):
    """把 legacy_project fixture 拷进临时 projects 目录，返回指向它的 JsonRepository。"""
    projects = tmp_path / "projects"
    projects.mkdir()
    shutil.copytree(FIXTURE, projects / "legacy")
    return JsonRepository(projects_dir=projects)


def test_migrate_imports_all_seven_types(legacy_json_repo, tmp_path):
    sqlite = SqliteRepository(db_path=tmp_path / "fieldwork.db")
    report = migrate_to_sqlite(legacy_json_repo, sqlite)
    assert report["projects"] >= 1
    # 规范化核心 state 全等（迁移的 7 类）
    for pid in [p["project_id"] for p in legacy_json_repo.list_projects()]:
        js, ss = legacy_json_repo.get_state(pid), sqlite.get_state(pid)
        assert ss["phase"] == js["phase"]
        assert ss["research_context"] == js["research_context"]
        assert [m["content"] for m in sqlite.get_messages(pid, 0)] == \
               [m["content"] for m in legacy_json_repo.get_messages(pid, 0)]
        assert ss["field_sites"] == js["field_sites"]
        assert ss["informants"] == js["informants"]


def test_migrate_idempotent(legacy_json_repo, tmp_path):
    sqlite = SqliteRepository(db_path=tmp_path / "fieldwork.db")
    migrate_to_sqlite(legacy_json_repo, sqlite)
    migrate_to_sqlite(legacy_json_repo, sqlite)   # 再迁不产生重复
    pid = legacy_json_repo.list_projects()[0]["project_id"]
    assert len(sqlite.get_messages(pid, 0)) == len(legacy_json_repo.get_messages(pid, 0))


def test_migrate_marks_schema_meta(legacy_json_repo, tmp_path):
    sqlite = SqliteRepository(db_path=tmp_path / "fieldwork.db")
    migrate_to_sqlite(legacy_json_repo, sqlite)
    import sqlite3
    with sqlite3.connect(sqlite.db_path) as conn:
        row = conn.execute("SELECT value FROM schema_meta WHERE key='migrated_from'").fetchone()
    assert row and row[0] == "json"


def test_migrate_single_project(legacy_json_repo, tmp_path):
    sqlite = SqliteRepository(db_path=tmp_path / "fieldwork.db")
    pid = legacy_json_repo.list_projects()[0]["project_id"]
    report = migrate_to_sqlite(legacy_json_repo, sqlite, project_id=pid)
    assert report["projects"] == 1
```

（先 `Read tests/fixtures/legacy_project/state.json` 确认 fixture 的真实字段/项目 id，测试断言按实际 fixture 调整——若 fixture 无 field_sites/informants，相应断言改为「迁移后与 Json 侧同为空」。）

- [ ] **Step 2: 确认失败** → **Step 3: 实现** `core/migrate.py` + SqliteRepository 的 raw-import 方法

**关键：迁移不能用公开 `append_message`/`save_field_site`/`add_knowledge`/`save_proposal`/`cache_search`**——它们会重盖时间戳（`saved_at/ts=_now()`）或生成新 id，导致迁移后记录与 Json 侧不等（破坏“规范化 state 全等”与幂等）。故本 Task 给 SqliteRepository 加一组 **raw-import 内部方法**，原样插入完整记录（保留 id/saved_at/ts/created_at/cached_at/filename 与任意字段）：

```python
# sqlite_repo.py 新增（仅供迁移调用，不属 Protocol）：
def _import_phase_history(self, pid, history): ...                   # 原样写存量 phase_history JSON
def _import_message(self, pid, role, content, meta, ts): ...      # INSERT 保留原 ts
def _import_record(self, table, pid, entry): ...                   # field_sites/informants：保留 entry 原 id+saved_at+seq
def _import_knowledge(self, pid, entry): ...                       # 保留原 id+created_at+seq
def _import_proposal(self, pid, entry): ...                        # 保留原 id+filename+saved_at+content+seq
def _import_cache(self, pid, entry): ...                           # 保留原 id+query+tool+result+cached_at
```
（各 `_import_*` 用 `INSERT OR REPLACE` 保证幂等再迁不重复；seq 按插入顺序递增。entry 是 Json 侧读出的原 dict，原样 `json.dumps` 入 data 列或拆列入库。）

```python
"""JSON → SQLite 存量搬迁（spec §5.4 里程碑3）。幂等；JSON 目录原地保留作回滚。"""
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository


def migrate_to_sqlite(json_repo: JsonRepository, sqlite_repo: SqliteRepository,
                      project_id: str | None = None) -> dict:
    pids = [project_id] if project_id else [p["project_id"] for p in json_repo.list_projects()]
    report = {"projects": 0, "messages": 0, "field_sites": 0, "informants": 0,
              "search_cache": 0, "knowledge": 0, "proposals": 0}
    for pid in pids:
        jstate = json_repo.get_state(pid)
        sqlite_repo.create(pid)                              # 幂等建项目
        sqlite_repo.set_phase(pid, jstate["phase"])          # 阶段（同阶段不写史）
        # phase_history 原样搬迁（set_phase 只记本次转移，存量历史需 raw 写入）
        sqlite_repo._import_phase_history(pid, jstate["phase_history"])
        if jstate["research_context"]:
            sqlite_repo.update_research_context(pid, jstate["research_context"])
        for m in json_repo.get_messages(pid, limit=0):       # 全量消息，保留原 ts
            sqlite_repo._import_message(pid, m["role"], m["content"],
                                        m.get("metadata", {}), m["ts"])
            report["messages"] += 1
        for s in jstate["field_sites"]:
            sqlite_repo._import_record("field_sites", pid, s); report["field_sites"] += 1
        for i in jstate["informants"]:
            sqlite_repo._import_record("informants", pid, i); report["informants"] += 1
        for c in json_repo.list_cached_searches(pid):        # 缓存（取全量含 result）
            full = json_repo.get_cached_search(c["id"])
            sqlite_repo._import_cache(pid, full); report["search_cache"] += 1
        for k in json_repo.list_knowledge(pid):
            sqlite_repo._import_knowledge(pid, k); report["knowledge"] += 1
        for p in json_repo.list_proposals(pid):              # 含旧单值键兼容读出
            sqlite_repo._import_proposal(pid, p); report["proposals"] += 1
        report["projects"] += 1
    _mark_migrated(sqlite_repo)
    _verify(json_repo, sqlite_repo, pids)                    # 行数 + 抽样哈希；不一致抛 RuntimeError
    return report
```
（`_mark_migrated` 写 `schema_meta(migrated_from='json')`；`_verify` 逐项目比对行数 + 抽样字段哈希，失败抛 RuntimeError。因走 raw-import 保留原 id/时间戳，迁移后 `field_sites/informants/knowledge/proposals` 与 Json 侧逐字段全等，幂等再迁（INSERT OR REPLACE）不产生重复。旧单值键 proposal_draft/final_proposal 已由 JsonRepository.list_proposals 兼容读出，随 proposals 一并迁入。）

- [ ] **Step 4: CLI 接线** `core/cli.py` 加 `migrate` 子命令（argparse 或现有 CLI 框架）：`fieldwork migrate --to-sqlite [--project-id X]` → 构造 json_repo（Config.projects_dir）+ sqlite_repo（Config.sqlite_path）→ 调 `migrate_to_sqlite` → 打印 report。先 `Read core/cli.py` 确认现有命令注册方式再对齐接线。

- [ ] **Step 5: 文档** README.md 加「存储后端与迁移」段（`FA_STORAGE=sqlite` 切换、`fieldwork migrate --to-sqlite`、迁移期单写者说明、JSON 只读回退）；DEVELOPMENT.md 扩展点补「新增 Repository 实现只须过 repository_contract 双跑」。

- [ ] **Step 6: 验证** — `pytest tests/test_migrate.py -q` + 全量 `pytest -q`（无回归）+ `ruff check .`

- [ ] **Step 7: Commit** — `git commit -m "feat(repo): fieldwork migrate --to-sqlite存量搬迁(里程碑3)+文档"`

---

## 验收（整盘）

1. `pytest tests/repository_contract.py -q`：30 契约用例 × Json/Sqlite 双实现全绿（里程碑 2 平价）。
2. `FA_STORAGE=sqlite` 下 Web 宿主与 MCP Server 均可跑通既有宿主测试（`test_web_*`/`test_mcp_*` 用工厂构造 sqlite 后端的变体，或至少 factory 单测覆盖）。
3. `fieldwork migrate --to-sqlite` 对 `tests/fixtures/legacy_project` 迁移后规范化核心 state 全等、幂等再迁不重复、schema_meta 标记 migrated_from。
4. 默认（FA_STORAGE 未设）行为零变化：JsonRepository 仍是默认后端，所有既有测试不动即绿。
5. `ruff check .` 干净；CI 双平台矩阵全过。
6. WAL 模式生效（`PRAGMA journal_mode`=wal）；SqliteRepository 实例可随时重建、跨实例读到库中真相。

## 假设与待确认

- **裁决1（FTS5 延后）**与**裁决2（JSON data 列）**是对 spec §5.2 字面 schema 的有意偏离，理由见「关键设计裁决」。若你坚持 spec 的 FTS5 + 命名列，需同步放宽契约（search_knowledge 改全文语义、迁移放弃 state 全等改抽样）——执行前请确认。
- 迁移保留原记录 id（field_sites/informants/knowledge/proposals）以支持幂等再迁；`add_knowledge` 可能需加可选 `entry_id` 参数（Task 10 Step 3 注明）。
- 本计划**不含** TurnManager 回合事件的 SQLite 化（spec §3.3 触发条件未满足，仍用 JSON）；不含 FTS5/向量检索（spec §7 后续）。
- **并发提醒**：另一 agent 正在同仓做 asset-tiering（kernel/assets.py、kernel/guidance/informants.py、secrets/、core/config.py 可能涉及）。Plan 5 执行时若与其在 `core/config.py` 撞车，以先落地方为准、后者 rebase；本计划只**新增** config 字段（storage_backend/sqlite_path），不改其既有字段，冲突面小。

