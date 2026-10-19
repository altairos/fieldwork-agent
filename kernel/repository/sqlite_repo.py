"""``SqliteRepository`` — ``ProjectRepository`` 的 SQLite 实现（spec §5 里程碑 2）。

与 JsonRepository 逐行为对齐（由 tests/repository_contract.py 双跑钉死）。要点：
- stdlib sqlite3、无 ORM；PRAGMA user_version 管 schema 版本；
- WAL + busy_timeout=5000 支持 MCP/Web 双进程并发读；
- 每次操作开新连接（context manager），实例可随时重建、无缓存；
- 记录类（田野点/报道人）以 JSON data 列存整条记录，保证 schemaless dict 平价。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from fieldwork_agent.kernel.guidance.phases import PHASES
from fieldwork_agent.kernel.models import ConfirmationRecord
from fieldwork_agent.kernel.repository.cache_summary import result_summary
from fieldwork_agent.kernel.repository.confirm_hash import (
    ProposalConfirmError,
    compute_confirm_hash,
)
from fieldwork_agent.kernel.repository.guard import cache_id_violation, owner_violation
from fieldwork_agent.kernel.repository.protocol import _UNSET

SCHEMA_VERSION = 5
MESSAGE_WINDOW = 50
CACHE_LIMIT_BYTES = 64 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id    TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    phase         TEXT NOT NULL,
    phase_history TEXT NOT NULL DEFAULT '[]',   -- JSON array
    proposal_confirmed INTEGER NOT NULL DEFAULT 0,   -- 审计二 R09：方案确认态
    owner         TEXT                          -- 归属（anon:<token>；NULL=无主）归属隔离
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
    id         TEXT NOT NULL,                   -- id 项目内作用域（对齐 json；跨项目同 id 共存）
    project_id TEXT NOT NULL,
    saved_at   TEXT NOT NULL,
    seq        INTEGER NOT NULL,                -- 保存序（list 稳定排序）
    data       TEXT NOT NULL,                   -- JSON 整条记录（裁决2）
    PRIMARY KEY (project_id, id),
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sites_project ON field_sites(project_id, seq);

CREATE TABLE IF NOT EXISTS informants (
    id         TEXT NOT NULL,                   -- id 项目内作用域（对齐 json；跨项目同 id 共存）
    project_id TEXT NOT NULL,
    saved_at   TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    data       TEXT NOT NULL,                   -- JSON 整条记录
    PRIMARY KEY (project_id, id),
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_informants_project ON informants(project_id, seq);

CREATE TABLE IF NOT EXISTS search_cache (
    id         TEXT NOT NULL,                   -- N07：项目内作用域（跨项目同 legacy cid 共存）
    project_id TEXT NOT NULL,
    query      TEXT NOT NULL,
    tool       TEXT NOT NULL,
    result     TEXT NOT NULL,                   -- JSON；写入前已应用 64KB 截断
    cached_at  TEXT NOT NULL,
    parent_call_id TEXT,
    turn_id    TEXT,
    query_key  TEXT,
    is_empty   INTEGER,
    is_truncated INTEGER,
    total      INTEGER,
    PRIMARY KEY (project_id, id),
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cache_project ON search_cache(project_id, cached_at);

CREATE TABLE IF NOT EXISTS knowledge (
    id         TEXT NOT NULL,                   -- id 项目内作用域（对齐 json；跨项目同 id 共存）
    project_id TEXT NOT NULL,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'user',
    category   TEXT NOT NULL DEFAULT 'general',
    tags       TEXT NOT NULL DEFAULT '[]',      -- JSON array
    created_at TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    PRIMARY KEY (project_id, id),
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_project ON knowledge(project_id, seq);

CREATE TABLE IF NOT EXISTS proposals (
    id         TEXT NOT NULL,                   -- id 项目内作用域（对齐 json；跨项目同 id 共存）
    project_id TEXT NOT NULL,
    kind       TEXT NOT NULL,
    version    TEXT NOT NULL,
    filename   TEXT NOT NULL,                   -- 合成导出名（裁决4）
    content    TEXT NOT NULL,                   -- 事实源（spec §5.3）
    saved_at   TEXT NOT NULL,
    dep_hash   TEXT NOT NULL DEFAULT '',        -- 五路依赖指纹；空串 = legacy 无指纹
    seq        INTEGER NOT NULL,
    PRIMARY KEY (project_id, id),
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_proposals_project ON proposals(project_id, seq);

CREATE TABLE IF NOT EXISTS confirmations (
    id           TEXT NOT NULL,                   -- 内容规范哈希（幂等去重键）
    project_id   TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT '',
    subject      TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT NOT NULL DEFAULT '',
    inputs_hash  TEXT NOT NULL DEFAULT '',
    dep_hash     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, id),
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_confirmations_project ON confirmations(project_id);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# 跨版本增量列（存量行由 DEFAULT 值补齐，不改任何既有语义）：
# - v1→v2（审计二 R02）：projects.proposal_confirmed、proposals.dep_hash
# - v2→v3（归属隔离）：projects.owner
# - v3→v4（N07）：search_cache 主键由全局 id 改为 (project_id, id)——SQLite
#   不能 ALTER 主键，在 _ensure_schema 里检测旧形状则整表重建（见下）。
# - v3→v4（spec C5 证据索引）：search_cache 六列（可空，NULL=unknown，R12）
# - v4→v5（合并）：N07 复合主键与 C5 六列在两条分支上各自为 v4、互缺对方
#   改动；合并版升 v5，任一形状的 v4 库重开都走补列+重建（均幂等）补齐。
# confirmations 表与索引已含于 _SCHEMA（IF NOT EXISTS，_ensure_schema 对旧库
# 在版本分支之前即已建好）；执行见 _ensure_schema 的显式事务
# （补列与 user_version 同生共死，防「列已加、版本未进」的半迁移库重开锁死）。
_MIGRATE_COLUMNS = (
    ("projects", "proposal_confirmed",
     "ALTER TABLE projects ADD COLUMN proposal_confirmed INTEGER NOT NULL DEFAULT 0"),
    ("proposals", "dep_hash",
     "ALTER TABLE proposals ADD COLUMN dep_hash TEXT NOT NULL DEFAULT ''"),
    ("projects", "owner",
     "ALTER TABLE projects ADD COLUMN owner TEXT"),
    ("search_cache", "parent_call_id", "ALTER TABLE search_cache ADD COLUMN parent_call_id TEXT"),
    ("search_cache", "turn_id", "ALTER TABLE search_cache ADD COLUMN turn_id TEXT"),
    ("search_cache", "query_key", "ALTER TABLE search_cache ADD COLUMN query_key TEXT"),
    ("search_cache", "is_empty", "ALTER TABLE search_cache ADD COLUMN is_empty INTEGER"),
    ("search_cache", "is_truncated", "ALTER TABLE search_cache ADD COLUMN is_truncated INTEGER"),
    ("search_cache", "total", "ALTER TABLE search_cache ADD COLUMN total INTEGER"),
)


def _now() -> str:
    return datetime.now().isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}"


def _legacy_id(entry: dict) -> str:
    """legacy 无 id 记录的确定性占位 id：内容规范哈希（重跑 REPLACE 天然去重）。"""
    canonical = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    return "legacy_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _with_dep_hash(entry: dict, dep_hash: str) -> dict:
    """非空才挂 `dep_hash` 键：legacy 行（空串）保持与 json 侧旧记录相同的键集，
    这是迁移后逐字段全等（`_verify` 首条哈希比对）的前提。"""
    if dep_hash:
        entry["dep_hash"] = dep_hash
    return entry


class SqliteRepository:
    """以单文件 SQLite 库为后端的持久化实现（FA_STORAGE=sqlite 时启用）。"""

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)
        self._tx_conn: sqlite3.Connection | None = None   # N06：环境事务连接（见 transaction）
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def root(self) -> Path:
        """库文件父目录（非 Protocol 成员）：宿主 /ready 可写探针与 ``guard``
        的 containment 校验都经 ``getattr(repo, "root", None)`` 取根，sqlite
        后端同样得给出一个真实受信目录，否则探针退化为“不可写”。"""
        return self._db_path.parent

    @contextmanager
    def _connect(self, immediate: bool = False):
        """打开一次性连接。``immediate=True`` 时以 BEGIN IMMEDIATE 开写事务：
        SELECT 前即取 RESERVED 写锁，read-modify-write（字段合并/阶段史/
        序号分配/确认校验）全程独占，杜绝双进程并发丢更新（审计 R04）。

        并发写者在 busy_timeout 内排队等锁；读路径保持 deferred（WAL 下
        读不阻塞）。「_ensure_schema 不用 immediate：executescript 会隐式
        COMMIT 掉手动事务。」

        N06：若已处于 ``transaction()`` 环境事务中（``_tx_conn`` 非空），复用同一
        连接、不再单独 BEGIN/COMMIT/CLOSE——让一组写操作（迁移的清空+导入+
        验证+标记）落在单事务里，任一步失败整体回滚到迁移前状态。
        """
        if self._tx_conn is not None:
            yield self._tx_conn
            return
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        """N06：把一组写操作包进「单连接单事务」（BEGIN IMMEDIATE … COMMIT/ROLLBACK）。

        期间所有经 ``_connect`` 的读写都复用同一连接：写方法不再各自提交，读方法
        （含 ``migrate._verify``）读到的是本事务未提交的中间态，可在 COMMIT 前校验。
        任一步抛异常 → 整体 ROLLBACK，库回到进入前的状态（force 迁移「清空+导入」
        的原子性即靠此保证）。不可重入：嵌套调用直接报错，避免半提交歧义。
        """
        if self._tx_conn is not None:
            raise RuntimeError("已有进行中的 transaction()，不可重入")
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            self._tx_conn = conn
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            self._tx_conn = None
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            cur = conn.execute("PRAGMA user_version").fetchone()[0]
            if cur > SCHEMA_VERSION:
                raise RuntimeError(
                    f"数据库 schema 版本 {cur} 高于当前代码支持的 {SCHEMA_VERSION}："
                    "请先升级 fieldwork-agent")
            conn.executescript(_SCHEMA)   # 全 IF NOT EXISTS：对存量表是 no-op
            if cur == 0:
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            elif cur < SCHEMA_VERSION:
                # 显式单事务包住「补列 + 版本推进」：executescript 逐语句隐式提交，
                # 中途被杀留下「列已持久化、版本未推进」的半迁移库，重开即
                # duplicate column 锁死（终审 T2-M1）。缺列才补 = 半迁移库幂等恢复。
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for table, column, stmt in _MIGRATE_COLUMNS:
                        cols = {r[1] for r in conn.execute(
                            f"PRAGMA table_info({table})")}
                        if column not in cols:
                            conn.execute(stmt)
                    self._rebuild_search_cache_pk(conn)   # N07：复合主键重建（幂等；v3/v4 旧形状库都走此路径）
                    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                    conn.execute("COMMIT")
                except Exception:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    raise
            # 归属索引（spec 为 owner 过滤路径预留）：不能进 _SCHEMA——旧库重开时
            # executescript 先于下方迁移分支执行，彼时 owner 列尚不存在，建索引会
            # 直接报 no such column；放这里在补列之后幂等补建（新库/旧库殊途同归）。
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner)")

    @staticmethod
    def _rebuild_search_cache_pk(conn) -> None:
        """N07：search_cache 主键由全局 ``id`` 重建为 ``(project_id, id)``（v3→v4 引入）。

        合并版 v4→v5 仍走此路径：C5 六列先由 _MIGRATE_COLUMNS 补齐，
        重建的建表与搬数据均含六列，不丢证据索引数据。

        SQLite 不能 ALTER 主键，采标准建新表→搬数据→drop→rename 四步（均用
        conn.execute 而非 executescript，后者会隐式 COMMIT 掉外层事务）。
        幂等：已是复合主键（project_id 的 pk 序>0）则直接返回；无入向 FK
        引用 search_cache，foreign_keys=ON 下 drop/rename 安全。
        """
        pk_ord = {r[1]: r[5] for r in conn.execute("PRAGMA table_info(search_cache)")}
        if pk_ord.get("project_id", 0) > 0:
            return   # 已是复合主键（新库/已重建），无需动作
        conn.execute(
            "CREATE TABLE search_cache_new ("
            "id TEXT NOT NULL, project_id TEXT NOT NULL, query TEXT NOT NULL,"
            " tool TEXT NOT NULL, result TEXT NOT NULL, cached_at TEXT NOT NULL,"
            " parent_call_id TEXT, turn_id TEXT, query_key TEXT,"
            " is_empty INTEGER, is_truncated INTEGER, total INTEGER,"
            " PRIMARY KEY (project_id, id),"
            " FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE)")
        conn.execute(
            "INSERT INTO search_cache_new(id, project_id, query, tool, result, cached_at,"
            " parent_call_id, turn_id, query_key, is_empty, is_truncated, total)"
            " SELECT id, project_id, query, tool, result, cached_at,"
            " parent_call_id, turn_id, query_key, is_empty, is_truncated, total"
            " FROM search_cache")
        conn.execute("DROP TABLE search_cache")
        conn.execute("ALTER TABLE search_cache_new RENAME TO search_cache")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cache_project"
            " ON search_cache(project_id, cached_at)")

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

    # ------------------------------------------------------------------ 项目与阶段

    def create(self, project_id: str, owner: str | None = None) -> None:
        """幂等建项目：已存在则原样保留存量（INSERT OR IGNORE 天然不覆盖 owner）。"""
        reason = owner_violation(owner)
        if reason:
            raise ValueError(f"非法 owner（{reason}）: {owner}")
        now = _now()
        with self._connect(immediate=True) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO projects(project_id,created_at,updated_at,phase,phase_history,owner)"
                " VALUES(?,?,?,?,?,?)",
                (project_id, now, now, PHASES[0], "[]", owner))
            conn.execute(
                "INSERT OR IGNORE INTO research_context(project_id,data) VALUES(?,?)",
                (project_id, "{}"))

    def delete_project(self, pid: str) -> None:
        """删除即整域清除：全部子表 FK 均 ON DELETE CASCADE，删主行即级联清空。"""
        self._require(pid)
        with self._connect(immediate=True) as conn:
            conn.execute("DELETE FROM projects WHERE project_id=?", (pid,))

    def list_projects(self, owner=_UNSET) -> list[dict]:
        """项目摘要列表：owner 三态（缺省全量 / None 仅无主 / str 仅该归属）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT project_id, owner, phase, created_at, updated_at FROM projects"
                " ORDER BY project_id").fetchall()
        return [
            {
                "project_id": r["project_id"],
                "phase": r["phase"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
            if owner is _UNSET or r["owner"] == owner   # SQL NULL ↔ Python None
        ]

    def set_owner(self, pid: str, owner: str | None) -> None:
        """改写归属（账号认领预留）；幂等（同值直接返回，不动 updated_at）；未知项目 KeyError。"""
        reason = owner_violation(owner)
        if reason:
            raise ValueError(f"非法 owner（{reason}）: {owner}")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner FROM projects WHERE project_id=?", (pid,)).fetchone()
            if row is None:
                raise KeyError(f"未知项目: {pid}")
            if row["owner"] == owner:
                return
            conn.execute(
                "UPDATE projects SET owner=?, updated_at=? WHERE project_id=?",
                (owner, _now(), pid))

    def get_state(self, pid: str) -> dict:
        """完整状态 dict（键序对齐 JsonRepository._default_state），每次现读、全新副本。"""
        self._require(pid)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT project_id, created_at, updated_at, phase, phase_history,"
                " proposal_confirmed, owner FROM projects WHERE project_id=?",
                (pid,)).fetchone()
            ctx_row = conn.execute(
                "SELECT data FROM research_context WHERE project_id=?", (pid,)).fetchone()
            window = conn.execute(
                "SELECT role, content, meta, ts FROM messages WHERE project_id=?"
                " ORDER BY id DESC LIMIT ?",
                (pid, MESSAGE_WINDOW)).fetchall()
            sites = conn.execute(
                "SELECT data FROM field_sites WHERE project_id=? ORDER BY seq",
                (pid,)).fetchall()
            informants = conn.execute(
                "SELECT data FROM informants WHERE project_id=? ORDER BY seq",
                (pid,)).fetchall()
            proposal_rows = conn.execute(
                "SELECT id, kind, version, filename, saved_at, dep_hash FROM proposals"
                " WHERE project_id=? ORDER BY seq",
                (pid,)).fetchall()
            conf_rows = conn.execute(
                "SELECT kind, subject, confirmed_at, inputs_hash, dep_hash"
                " FROM confirmations WHERE project_id=? ORDER BY rowid",
                (pid,)).fetchall()
        return {
            "project_id": row["project_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "phase": row["phase"],
            "phase_history": json.loads(row["phase_history"]),
            "owner": row["owner"],
            "research_context": json.loads(ctx_row["data"]) if ctx_row else {},
            # 窗口装配：最近 ≤MESSAGE_WINDOW 条，正序返回（单轨真源在 messages 表）
            "dialogue_context": [
                {"role": r["role"], "content": r["content"], "ts": r["ts"],
                 "metadata": json.loads(r["meta"])}
                for r in reversed(window)
            ],
            "field_sites": [json.loads(r["data"]) for r in sites],
            "informants": [json.loads(r["data"]) for r in informants],
            # 记录不含 content（content 事实源由 list_proposals 按需读取）
            "proposals": [
                _with_dep_hash(
                    {"id": r["id"], "kind": r["kind"], "version": r["version"],
                     "filename": r["filename"], "saved_at": r["saved_at"]},
                    r["dep_hash"])
                for r in proposal_rows
            ],
            # 确认记录键集 == ConfirmationRecord.model_dump(mode="json")（不含表内 id）
            "confirmations": [
                _with_dep_hash(
                    {"kind": r["kind"], "subject": r["subject"],
                     "confirmed_at": r["confirmed_at"],
                     "inputs_hash": r["inputs_hash"]},
                    r["dep_hash"])
                for r in conf_rows
            ],
            "proposal_confirmed": bool(row["proposal_confirmed"]),
            # legacy 空默认（读兼容键，对齐 json_repo _default_state）
            "travel_plan": None,
            "researcher_info": {},
            "proposal_draft": None,
            "final_proposal": None,
            "search_results": [],
            "notes": [],
        }

    def get_phase(self, pid: str) -> str:
        self._require(pid)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT phase FROM projects WHERE project_id=?", (pid,)).fetchone()
        return row["phase"]

    def set_phase(self, pid: str, phase: str) -> None:
        if phase not in PHASES:
            raise ValueError(f"未知阶段: {phase}")
        now = _now()
        with self._connect(immediate=True) as conn:   # R04：阶段史读改写锁化
            row = conn.execute(
                "SELECT phase, phase_history FROM projects WHERE project_id=?",
                (pid,)).fetchone()
            if row is None:
                raise KeyError(f"未知项目: {pid}")
            current = row["phase"]
            if current == phase:   # 同阶段不写历史、不触碰 updated_at
                return
            history = json.loads(row["phase_history"])
            history.append({"from": current, "to": phase, "at": now})
            conn.execute(
                "UPDATE projects SET phase=?, phase_history=?, updated_at=?"
                " WHERE project_id=?",
                (phase, _dumps(history), now, pid))

    # ------------------------------------------------------------------ 研究上下文

    def update_research_context(self, pid: str, patch: dict) -> None:
        self._require(pid)
        now = _now()
        with self._connect(immediate=True) as conn:   # R04：读-合并-写全程持锁
            row = conn.execute(
                "SELECT data FROM research_context WHERE project_id=?", (pid,)).fetchone()
            data = json.loads(row["data"]) if row else {}
            data.update(patch)   # 浅合并（与 json_repo 的 dict.update 一致）
            conn.execute(
                "INSERT OR REPLACE INTO research_context(project_id,data) VALUES(?,?)",
                (pid, _dumps(data)))
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE project_id=?", (now, pid))

    def get_research_context(self, pid: str) -> dict:
        self._require(pid)
        with self._connect() as conn:
            return self._read_research_context(conn, pid)

    # ------------------------------------------------------------------ 消息史

    def append_message(self, pid: str, role: str, content: str, meta: dict | None = None) -> None:
        """追加一条到全量史（messages 表，单轨真源）；窗口由 get_state 现算，无需回写。"""
        self._require(pid)
        now = _now()
        with self._connect(immediate=True) as conn:
            conn.execute(
                "INSERT INTO messages(project_id, role, content, meta, ts)"
                " VALUES(?,?,?,?,?)",
                (pid, role, content, _dumps(dict(meta or {})), now))
            # 对齐 json_repo：消息变更也推进 updated_at（经 _save 落盘时刷新）
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE project_id=?", (now, pid))

    def get_messages(self, pid: str, limit: int = 30) -> list[dict]:
        """正序读全量史，返回最近 ``limit`` 条（``limit <= 0`` 取全部）。"""
        self._require(pid)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, meta, ts FROM messages WHERE project_id=?"
                " ORDER BY id",
                (pid,)).fetchall()
        messages = [
            {"role": r["role"], "content": r["content"], "ts": r["ts"],
             "metadata": json.loads(r["meta"])}
            for r in rows
        ]
        if limit <= 0:
            return messages
        return messages[-limit:]

    def pop_last_message(self, pid: str, role: str | None = None) -> dict | None:
        """弹出最近一条（可按 role 过滤）：按 id 倒序取一条后 DELETE 并推进 updated_at。"""
        self._require(pid)
        sql = "SELECT id, role, content, meta, ts FROM messages WHERE project_id=?"
        params: list = [pid]
        if role is not None:
            sql += " AND role=?"
            params.append(role)
        sql += " ORDER BY id DESC LIMIT 1"
        now = _now()
        with self._connect(immediate=True) as conn:
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM messages WHERE id=?", (row["id"],))
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE project_id=?", (now, pid))
        return {"role": row["role"], "content": row["content"], "ts": row["ts"],
                "metadata": json.loads(row["meta"])}

    # ------------------------------------------------------------------ 田野点与报道人

    def _upsert_json_record(self, conn, pid: str, record: dict,
                            table: str, prefix: str) -> dict:
        """JSON 整记录 upsert（裁决2）：同 id 更新保 seq，否则 INSERT seq=next。"""
        entry = dict(record)
        entry["id"] = record.get("id") or _new_id(prefix)
        entry["saved_at"] = _now()
        exists = conn.execute(
            f"SELECT 1 FROM {table} WHERE project_id=? AND id=?",
            (pid, entry["id"])).fetchone()
        if exists is not None:
            conn.execute(
                f"UPDATE {table} SET data=?, saved_at=? WHERE project_id=? AND id=?",
                (_dumps(entry), entry["saved_at"], pid, entry["id"]))
        else:
            # 带 id 但行不存在 = 新增（保留调用者的 id），与 json_repo upsert 语义平价
            conn.execute(
                f"INSERT INTO {table}(id, project_id, saved_at, seq, data)"
                " VALUES(?,?,?,?,?)",
                (entry["id"], pid, entry["saved_at"],
                 self._next_seq(conn, table, pid), _dumps(entry)))
        return entry

    def save_field_site(self, pid: str, record: dict) -> str:
        self._require(pid)
        with self._connect(immediate=True) as conn:   # R04：存在性检查+seq 分配锁化
            entry = self._upsert_json_record(conn, pid, record, "field_sites", "site")
            # 对齐 json_repo：记录变更同事务推进 updated_at（json 侧经 _save 刷新）
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE project_id=?",
                (entry["saved_at"], pid))
        return entry["id"]

    def list_field_sites(self, pid: str) -> list[dict]:
        self._require(pid)
        with self._connect() as conn:
            return self._read_field_sites(conn, pid)

    def save_informant(self, pid: str, record: dict) -> str:
        self._require(pid)
        with self._connect(immediate=True) as conn:   # R04：存在性检查+seq 分配锁化
            entry = self._upsert_json_record(conn, pid, record, "informants", "inf")
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE project_id=?",
                (entry["saved_at"], pid))
        return entry["id"]

    def list_informants(self, pid: str) -> list[dict]:
        self._require(pid)
        with self._connect() as conn:
            return self._read_informants(conn, pid)

    # ------------------------------------------------------------------ 搜索缓存

    def cache_search(self, pid: str, query: str, tool: str, result: dict, *,
                     parent_call_id: str = "", turn_id: str = "",
                     query_key: str = "") -> str:
        """一次性 INSERT（cid 全局唯一、无更新语义，故不走 _upsert_json_record）。

        result 超 64KB 整体替换为截断标记；对齐 json_repo：缓存不经 _save 落盘，
        不推进 projects.updated_at。写入时派生索引摘要（R3/R12）：empty/truncated/
        total 落列（布尔存 INTEGER、None 存 NULL），索引查询免回读 result blob。
        """
        self._require(pid)
        cid = _new_id("srch")
        cached_at = _now()
        serialized = _dumps(result)
        if len(serialized.encode("utf-8")) > CACHE_LIMIT_BYTES:
            # 超限：整体替换为截断标记，preview 按 UTF-8 字节截断（不产生半个字符）
            preview = serialized.encode("utf-8")[:CACHE_LIMIT_BYTES].decode(
                "utf-8", errors="ignore")
            stored = {"truncated": True, "preview": preview}
            is_truncated, is_empty, total = True, False, None   # R5/R12：已知截断
        else:
            stored = result
            is_empty, total = result_summary(result)
            is_truncated = False
        with self._connect(immediate=True) as conn:
            conn.execute(
                "INSERT INTO search_cache(id, project_id, query, tool, result, cached_at,"
                " parent_call_id, turn_id, query_key, is_empty, is_truncated, total)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, pid, query, tool, _dumps(stored), cached_at,
                 parent_call_id, turn_id, query_key,
                 int(is_empty), int(is_truncated), total))
        return cid

    def list_cached_searches(self, pid: str) -> list[dict]:
        """缓存索引（不含 result；调用方按 cid 再取全文）。"""
        self._require(pid)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, query, tool, cached_at, parent_call_id, turn_id,"
                " query_key, is_empty, is_truncated, total FROM search_cache"
                " WHERE project_id=? ORDER BY cached_at, id",
                (pid,)).fetchall()
        return [
            {"id": r["id"], "query": r["query"], "tool": r["tool"],
             "cached_at": r["cached_at"],
             "parent_call_id": r["parent_call_id"] or "",
             "turn_id": r["turn_id"] or "",
             "query_key": r["query_key"] or "",
             "empty": None if r["is_empty"] is None else bool(r["is_empty"]),
             "truncated": None if r["is_truncated"] is None else bool(r["is_truncated"]),
             "total": r["total"]}
            for r in rows
        ]

    def get_cached_search(self, pid: str, cid: str) -> dict | None:
        """按项目域读取快照（审计二 R01）：cid 先过白名单，再只在 ``pid`` 域内定位。

        旧版以全局 PK 跳项目定位（同一库内可拿到他人快照）；新签名与
        ``json_repo`` 逐字一致：非法 cid / 跨项目 / 不存在均返回 ``None``（不抛）。
        """
        if cache_id_violation(cid):
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, query, tool, result, cached_at FROM search_cache"
                " WHERE project_id=? AND id=?",
                (pid, cid)).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "query": row["query"],
            "tool": row["tool"],
            "result": json.loads(row["result"]),
            "cached_at": row["cached_at"],
        }

    # ------------------------------------------------------------------ 知识库

    def add_knowledge(
        self,
        pid: str,
        title: str,
        content: str,
        source: str = "user",
        tags: list[str] | None = None,
        category: str = "general",
    ) -> str:
        """整条 INSERT（带 seq）；对齐 json_repo：知识条目直接落 knowledge 表、
        不经 _save 等价路径，不推进 projects.updated_at。
        """
        self._require(pid)
        entry_id = _new_id("know")
        with self._connect(immediate=True) as conn:   # R04：seq 分配锁化
            conn.execute(
                "INSERT INTO knowledge(id, project_id, title, content, source,"
                " category, tags, created_at, seq) VALUES(?,?,?,?,?,?,?,?,?)",
                (entry_id, pid, title, content, source, category,
                 _dumps(list(tags or [])), _now(),
                 self._next_seq(conn, "knowledge", pid)))
        return entry_id

    def list_knowledge(self, pid: str) -> list[dict]:
        self._require(pid)
        with self._connect() as conn:
            return self._read_knowledge(conn, pid)

    def search_knowledge(self, pid: str, text: str) -> list[dict]:
        """标题+正文+标签拼检索串，空白分词后要求全部子串命中（裁决1：非 FTS5，
        FTS5 无法命中短中文 token）。取 list_knowledge 后 Python 侧过滤，过滤
        逻辑与 json_repo 逐字一致；未知项目经 list_knowledge 的 _require 报
        KeyError（json 侧同为间接触发，平价）。
        """
        tokens = [t for t in text.lower().split() if t]
        if not tokens:
            return self.list_knowledge(pid)
        matched = []
        for entry in self.list_knowledge(pid):
            haystack = " ".join(
                [entry.get("title", ""), entry.get("content", ""), " ".join(entry.get("tags", []))]
            ).lower()
            if all(token in haystack for token in tokens):
                matched.append(entry)
        return matched

    def delete_knowledge(self, pid: str, entry_id: str) -> None:
        """不存在不报错（幂等）；对齐 json_repo：无 _require、不推 updated_at。"""
        with self._connect(immediate=True) as conn:
            conn.execute(
                "DELETE FROM knowledge WHERE project_id=? AND id=?", (pid, entry_id))

    def update_knowledge(
        self,
        pid: str,
        entry_id: str,
        title: str | None = None,
        content: str | None = None,
    ) -> dict:
        """对齐 json_repo：编辑不存在 → KeyError（UPDATE 0 行或读回无果同报）；
        未指定字段不动；不推 updated_at。"""
        with self._connect(immediate=True) as conn:
            if title is not None or content is not None:
                sets, params = [], []
                if title is not None:
                    sets.append("title=?")
                    params.append(title)
                if content is not None:
                    sets.append("content=?")
                    params.append(content)
                cur = conn.execute(
                    f"UPDATE knowledge SET {', '.join(sets)}"
                    " WHERE project_id=? AND id=?",
                    (*params, pid, entry_id))
                if cur.rowcount == 0:
                    raise KeyError(entry_id)
            for entry in self._read_knowledge(conn, pid):
                if entry["id"] == entry_id:
                    return entry
        raise KeyError(entry_id)

    # ------------------------------------------------------------------ 提案文档

    def save_proposal(
        self, pid: str, markdown: str, kind: str = "proposal", version: str = "draft",
        *, provenance_ctx: dict | None = None,
    ) -> str:
        """版本累积：每次 INSERT 新记录，绝不覆盖既有（契约要求）。

        对齐 json_repo：登记走 state["proposals"] + _save 落盘 → 同事务推进
        projects.updated_at；content 事实源存 DB（裁决4），不再落 .md 文件。
        N04：provenance_ctx 非 None 时，依赖指纹的 ctx 路用生成时实际值。
        """
        self._require(pid)
        record_id = _new_id("prop")
        uid = record_id.rsplit("_", 1)[-1]   # 与 id 同源（json 侧 id/后缀共用 uid）
        filename = f"{kind}_{version}_{datetime.now():%Y%m%d_%H%M%S}.md"
        saved_at = _now()
        with self._connect(immediate=True) as conn:   # R04：指纹+存在性检查+seq 分配同锁
            dep_hash = self._current_confirm_hash(
                pid, conn=conn, provenance_ctx=provenance_ctx)
            exists = conn.execute(
                "SELECT 1 FROM proposals WHERE project_id=? AND filename=?",
                (pid, filename)).fetchone()
            if exists is not None:
                # 同秒重复保存：追加随机后缀，绝不覆盖既有文档（版本累积是契约要求）
                filename = filename[:-3] + f"_{uid}.md"
            conn.execute(
                "INSERT INTO proposals(id, project_id, kind, version, filename,"
                " content, saved_at, dep_hash, seq) VALUES(?,?,?,?,?,?,?,?,?)",
                (record_id, pid, kind, version, filename, markdown, saved_at,
                 dep_hash, self._next_seq(conn, "proposals", pid)))
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE project_id=?", (saved_at, pid))
        return record_id

    def list_proposals(self, pid: str) -> list[dict]:
        """七键全量（含 content）；ORDER BY seq 平价 json 的保存序。"""
        self._require(pid)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, kind, version, filename, saved_at, content, dep_hash"
                " FROM proposals WHERE project_id=? ORDER BY seq",
                (pid,)).fetchall()
        return [
            {"id": r["id"], "kind": r["kind"], "version": r["version"],
             "filename": r["filename"], "saved_at": r["saved_at"],
             "content": r["content"], "dep_hash": r["dep_hash"]}
            for r in rows
        ]

    def get_latest_proposal(self, pid: str) -> dict | None:
        docs = self.list_proposals(pid)   # _require 经此传导（json 同为间接触发）
        return docs[-1] if docs else None

    # ------------------------------------------------ 确认依赖指纹（审计二 R09）

    def _latest_itinerary_cid(self, conn, pid: str) -> str:
        """最新一次 ``compose_itinerary`` 快照 id（无则空串）——五路指纹之⑤。

        走 ``_read_cached_search_index``（``ORDER BY cached_at, rowid``）倒取：
        Windows 时钟 tick ~15.6ms，同秒快照的 ``cached_at`` 必平局，只用它会
        退化成随机序（json 侧靠同秒单调 id 的文件名序规避，同一教训），
        rowid 是插入序，做兜底定序。
        """
        for cached in reversed(self._read_cached_search_index(conn, pid)):
            if cached.get("tool") == "compose_itinerary":
                return cached["id"]
        return ""

    def _current_confirm_hash(self, pid: str, conn=None, *,
                              provenance_ctx: dict | None = None) -> str:
        """五路依赖指纹（R09）。传 conn 时在该（写）事务内现读——确认校验/
        存档指纹与写入同锁原子（审计 R04：写锁覆盖状态 API）。

        N04：``provenance_ctx`` 非 None 时 ctx 路用生成时实际值（正文所本），
        其余四路仍现读仓储——使指纹忠于正文实际依赖而非保存时推测。
        """
        if conn is None:
            with self._connect() as ro:
                return self._current_confirm_hash(
                    pid, conn=ro, provenance_ctx=provenance_ctx)
        return compute_confirm_hash(
            provenance_ctx if provenance_ctx is not None
            else self._read_research_context(conn, pid),
            self._read_field_sites(conn, pid),
            self._read_informants(conn, pid),
            self._read_knowledge(conn, pid),
            self._latest_itinerary_cid(conn, pid))

    # ---------------------------------------- 确认与失效（审计二 R09，镜像 json_repo）

    def confirm_field_site(self, pid: str, site_id: str) -> bool:
        """按记录 id 把田野点置为已确认；未知 id 返回 ``False``（幂等）。

        对齐 json 侧：只改 ``confirmed``，没有动条目 ``saved_at``；项目级
        ``updated_at`` 推进（json 侧经 ``_save`` 刷新）。
        R04：读-改-写单事务（BEGIN IMMEDIATE）锁化。
        """
        self._require(pid)
        now = _now()
        with self._connect(immediate=True) as conn:   # R04：读-改-写锁化
            row = conn.execute(
                "SELECT data FROM field_sites WHERE project_id=? AND id=?",
                (pid, site_id)).fetchone()
            if row is None:
                return False
            entry = json.loads(row["data"])
            entry["confirmed"] = True
            conn.execute(
                "UPDATE field_sites SET data=? WHERE project_id=? AND id=?",
                (_dumps(entry), pid, site_id))
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE project_id=?", (now, pid))
        return True

    # 读 helper：公开读方法的单连接内联版（供写事务复用，R04）

    def _read_research_context(self, conn, pid: str) -> dict:
        row = conn.execute(
            "SELECT data FROM research_context WHERE project_id=?", (pid,)).fetchone()
        return json.loads(row["data"]) if row else {}

    def _read_field_sites(self, conn, pid: str) -> list[dict]:
        rows = conn.execute(
            "SELECT data FROM field_sites WHERE project_id=? ORDER BY seq",
            (pid,)).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def _read_informants(self, conn, pid: str) -> list[dict]:
        rows = conn.execute(
            "SELECT data FROM informants WHERE project_id=? ORDER BY seq",
            (pid,)).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def _read_knowledge(self, conn, pid: str) -> list[dict]:
        rows = conn.execute(
            "SELECT id, title, content, source, tags, category, created_at"
            " FROM knowledge WHERE project_id=? ORDER BY seq",
            (pid,)).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "content": r["content"],
                "source": r["source"],
                "tags": json.loads(r["tags"]),
                "category": r["category"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def _read_cached_search_index(self, conn, pid: str) -> list[dict]:
        rows = conn.execute(
            "SELECT id, query, tool, cached_at FROM search_cache WHERE project_id=?"
            " ORDER BY cached_at",
            (pid,)).fetchall()
        return [
            {"id": r["id"], "query": r["query"], "tool": r["tool"],
             "cached_at": r["cached_at"]}
            for r in rows
        ]

    def confirm_proposal(self, pid: str, doc_id: str = "") -> dict:
        """确认**指定**文档（``doc_id`` 空则取登记尾条，兼容旧调用面）。

        四重校验与错误文案与 ``json_repo.confirm_proposal`` 逐字一致（契约套件
        不校错误文案，但上游 API 把 ``.code`` 折成给用户的提示）。
        R04：校验读（含五路指纹）与写入同在 BEGIN IMMEDIATE 事务——
        防校验-写入竞态（写锁覆盖状态 API），不再跨两个连接。"""
        self._require(pid)
        with self._connect(immediate=True) as conn:
            if not doc_id:
                # P2-1：取“最新一份 proposal”，筛掉 interview_guide 等其他 kind
                # （对齐 json_repo；否则后存访谈指南会让空参确认误报 wrong_kind）
                row = conn.execute(
                    "SELECT id FROM proposals WHERE project_id=? AND kind='proposal'"
                    " ORDER BY seq DESC LIMIT 1",
                    (pid,)).fetchone()
                doc_id = row["id"] if row else ""
            record = conn.execute(
                "SELECT kind, dep_hash FROM proposals WHERE project_id=? AND id=?",
                (pid, doc_id)).fetchone()
            if record is None:
                raise ProposalConfirmError(
                    f"文档不存在: {doc_id or '（尚无文档）'}", "not_found")
            if record["kind"] != "proposal":
                raise ProposalConfirmError(
                    f"文档类型为 {record['kind']}，只能确认 proposal 文档", "wrong_kind")
            current = self._current_confirm_hash(pid, conn=conn)
            if not record["dep_hash"]:
                raise ProposalConfirmError(
                    "该文档缺少依赖指纹（旧版生成）：请重新生成后再确认", "dep_hash_missing")
            if record["dep_hash"] != current:
                raise ProposalConfirmError(
                    "研究输入已变化，该文档的确认已过期：请重新生成方案", "dep_hash_mismatch")
            rec = ConfirmationRecord(
                kind="proposal", subject=doc_id, confirmed_at=_now(), dep_hash=current)
            payload = rec.model_dump(mode="json")
            conn.execute(
                "INSERT OR REPLACE INTO confirmations(project_id, id, kind, subject,"
                " confirmed_at, inputs_hash, dep_hash) VALUES(?,?,?,?,?,?,?)",
                (pid, _legacy_id(payload), rec.kind, rec.subject, rec.confirmed_at,
                 rec.inputs_hash, rec.dep_hash))
            conn.execute(
                "UPDATE projects SET proposal_confirmed=1, updated_at=?"
                " WHERE project_id=?", (rec.confirmed_at, pid))
        return payload

    def get_proposal_by_id(self, pid: str, doc_id: str) -> dict | None:
        """按记录 id 取文档（含 ``content``，天然兼容 ``legacy_`` 前缀）。"""
        for record in self.list_proposals(pid):
            if record.get("id") == doc_id:
                return record
        return None

    def is_proposal_confirmation_stale(self, pid: str) -> bool:
        """无确认 → ``False``；确认对象不再是最新 proposal / 记录无指纹 /
        五路指纹与当前不一致 → ``True``（三级判定逐字镜像 json 侧）。

        新增 ``interview_guide`` 不使 proposal 确认失效：最新 id 只在
        ``kind='proposal'`` 行里取。
        """
        self._require(pid)
        with self._connect() as conn:
            conf = conn.execute(
                "SELECT subject, dep_hash FROM confirmations WHERE project_id=?"
                " ORDER BY rowid DESC LIMIT 1", (pid,)).fetchone()
            if conf is None:
                return False
            latest = conn.execute(
                "SELECT id FROM proposals WHERE project_id=? AND kind='proposal'"
                " ORDER BY seq DESC LIMIT 1", (pid,)).fetchone()
        subject, latest_id = conf["subject"], (latest["id"] if latest else "")
        if subject and latest_id and subject != latest_id:
            return True
        if not conf["dep_hash"]:
            return True
        return conf["dep_hash"] != self._current_confirm_hash(pid)

    # -------------------------------------------------- 迁移 raw-import（仅供 core.migrate 调用，不属 Protocol）
    #
    # 与公开写方法的区别：原样插入完整记录、保留 id/saved_at/ts/created_at/
    # cached_at/filename 等原值——公开方法会以 _now() 重盖时间戳或生成新 id，
    # 破坏迁移后与 JSON 侧的逐字段全等。幂等兜底用 INSERT OR REPLACE（messages
    # 例外：自增主键无稳定 id，先 _clear_messages 清空再整段重导）。

    def _import_phase_history(self, pid: str, history: list[dict]) -> None:
        """整列替换 phase_history 为 JSON 侧存量（原样，含原 at 值）。

        替换式是关键：迁移序里 create→set_phase 已写一条 at=now 的转移史，
        只有替换才能保证终态 == JSON 侧原样。
        """
        with self._connect(immediate=True) as conn:
            conn.execute(
                "UPDATE projects SET phase_history=? WHERE project_id=?",
                (_dumps(list(history or [])), pid))

    def _import_project_meta(self, pid: str, created_at: str, updated_at: str) -> None:
        """raw 回写项目时间戳（迁移保形；公开 create/set_phase 会以 _now() 重盖）。"""
        with self._connect(immediate=True) as conn:
            conn.execute(
                "UPDATE projects SET created_at=?, updated_at=? WHERE project_id=?",
                (created_at, updated_at, pid))

    def _clear_messages(self, pid: str) -> None:
        """清空该项目消息史（迁移中断重跑时先清空再重导，保证不产生重复行）。"""
        with self._connect(immediate=True) as conn:
            conn.execute("DELETE FROM messages WHERE project_id=?", (pid,))

    def _import_message(self, pid: str, role: str, content: str,
                        meta: dict, ts: str) -> None:
        """INSERT 一条消息，保留原 ts（不用 _now()）。"""
        with self._connect(immediate=True) as conn:
            conn.execute(
                "INSERT INTO messages(project_id, role, content, meta, ts)"
                " VALUES(?,?,?,?,?)",
                (pid, role, content, _dumps(dict(meta or {})), ts))

    def _import_record(self, table: str, pid: str, entry: dict) -> None:
        """field_sites/informants 整记录原样入库。

        - data 列存原 dict（list_field_sites/get_state 即 json.loads(data)，
          与 JSON 侧逐字段全等，不并入 id 列）；
        - 有原 id 用原 id；legacy 无 id 条目生成确定性占位 id（内容规范哈希），
          重跑 REPLACE 不重复；
        - saved_at 保留原值（legacy 缺失以空串占位满足 NOT NULL）。
        """
        entry = dict(entry)
        rid = entry.get("id") or _legacy_id(entry)
        saved_at = entry.get("saved_at") or ""
        with self._connect(immediate=True) as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {table}(id, project_id, saved_at, seq, data)"
                " VALUES(?,?,?,?,?)",
                (rid, pid, saved_at,
                 self._next_seq(conn, table, pid), _dumps(entry)))

    def _import_knowledge(self, pid: str, entry: dict) -> None:
        """知识条目原样入库（保留原 id+created_at+tags 等）。"""
        with self._connect(immediate=True) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO knowledge(id, project_id, title, content,"
                " source, category, tags, created_at, seq) VALUES(?,?,?,?,?,?,?,?,?)",
                (entry.get("id") or _legacy_id(entry), pid,
                 entry.get("title", ""), entry.get("content", ""),
                 entry.get("source", "user"), entry.get("category", "general"),
                 _dumps(list(entry.get("tags") or [])),
                 entry.get("created_at") or "",
                 self._next_seq(conn, "knowledge", pid)))

    def _import_proposal(self, pid: str, entry: dict) -> None:
        """提案整记录原样入库（保留原 id+filename+saved_at+content）。

        legacy 单值键读出的记录无 content 时填空串（content 列 NOT NULL；
        json 侧 _proposal_doc 对缺失文件同样以空串兜底）。
        """
        with self._connect(immediate=True) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO proposals(id, project_id, kind, version,"
                " filename, content, saved_at, dep_hash, seq) VALUES(?,?,?,?,?,?,?,?,?)",
                (entry.get("id") or _legacy_id(entry), pid,
                 entry.get("kind", "proposal"), entry.get("version", "draft"),
                 entry.get("filename", ""), entry.get("content") or "",
                 entry.get("saved_at") or "", entry.get("dep_hash", ""),
                 self._next_seq(conn, "proposals", pid)))

    def _import_cache(self, pid: str, entry: dict) -> None:
        """搜索缓存原样入库（保留原 id+query+result+cached_at）。

        旧缓存文件的 source_tool 读作 tool（对齐 json_repo list_cached_searches
        的读时归一）。C5/R12：派生列同步入库——legacy 文件无派生键时从 result
        现场派生（与 json 侧索引读时派生同构），core/migrate._verify 对
        search_cache 首条整条哈希全等依赖此对齐；三关联键缺省空串（与 json
        侧 ``data.get(...) or ""`` 读出平价）。
        """
        empty, truncated, total = (entry.get("empty"), entry.get("truncated"),
                                   entry.get("total"))
        if empty is None or truncated is None:   # legacy 文件 → 读侧同款现场派生
            result = entry.get("result")
            if isinstance(result, dict) and result.get("truncated") is True:
                truncated, empty, total = True, False, None
            else:
                empty, total = result_summary(result)
                truncated = False
        with self._connect(immediate=True) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO search_cache(id, project_id, query, tool,"
                " result, cached_at, parent_call_id, turn_id, query_key,"
                " is_empty, is_truncated, total) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (entry.get("id") or _legacy_id(entry), pid,
                 entry.get("query", ""),
                 entry.get("tool") or entry.get("source_tool", ""),
                 _dumps(entry.get("result") or {}),
                 entry.get("cached_at") or "",
                 entry.get("parent_call_id", ""), entry.get("turn_id", ""),
                 entry.get("query_key", ""),
                 int(empty), int(truncated), total))

    def _import_confirmation(self, pid: str, entry: dict) -> None:
        """确认记录原样入库（五字段全存，id 取内容哈希作幂等去重键）。

        ``get_state`` 读出时按 rowid（= 插入序）回到与 json 侧 `confirmations[]`
        同序的列表，且只返五个 model_dump 键。
        """
        payload = {"kind": entry.get("kind", ""), "subject": entry.get("subject", ""),
                   "confirmed_at": entry.get("confirmed_at", ""),
                   "inputs_hash": entry.get("inputs_hash", ""),
                   "dep_hash": entry.get("dep_hash", "")}
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO confirmations(project_id, id, kind, subject,"
                " confirmed_at, inputs_hash, dep_hash) VALUES(?,?,?,?,?,?,?)",
                (pid, _legacy_id(payload), payload["kind"], payload["subject"],
                 payload["confirmed_at"], payload["inputs_hash"], payload["dep_hash"]))

    def _set_proposal_confirmed(self, pid: str, value: bool) -> None:
        """raw 回写确认态（json 侧 ``state["proposal_confirmed"]`` 的列化）。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE projects SET proposal_confirmed=? WHERE project_id=?",
                (1 if value else 0, pid))
