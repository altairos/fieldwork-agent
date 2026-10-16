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
"""


def _now() -> str:
    return datetime.now().isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}"


def _dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


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

    # ------------------------------------------------------------------ 项目与阶段

    def create(self, project_id: str) -> None:
        """幂等建项目：已存在则原样保留存量（INSERT OR IGNORE），不覆盖、不报错。"""
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO projects(project_id,created_at,updated_at,phase,phase_history)"
                " VALUES(?,?,?,?,?)",
                (project_id, now, now, PHASES[0], "[]"))
            conn.execute(
                "INSERT OR IGNORE INTO research_context(project_id,data) VALUES(?,?)",
                (project_id, "{}"))

    def list_projects(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT project_id, phase, created_at, updated_at FROM projects"
                " ORDER BY project_id").fetchall()
        return [
            {
                "project_id": r["project_id"],
                "phase": r["phase"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def get_state(self, pid: str) -> dict:
        """完整状态 dict（键序对齐 JsonRepository._default_state），每次现读、全新副本。"""
        self._require(pid)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT project_id, created_at, updated_at, phase, phase_history"
                " FROM projects WHERE project_id=?", (pid,)).fetchone()
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
                "SELECT id, kind, version, filename, saved_at FROM proposals"
                " WHERE project_id=? ORDER BY seq",
                (pid,)).fetchall()
        return {
            "project_id": row["project_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "phase": row["phase"],
            "phase_history": json.loads(row["phase_history"]),
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
                {"id": r["id"], "kind": r["kind"], "version": r["version"],
                 "filename": r["filename"], "saved_at": r["saved_at"]}
                for r in proposal_rows
            ],
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
        with self._connect() as conn:
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
        with self._connect() as conn:
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
            row = conn.execute(
                "SELECT data FROM research_context WHERE project_id=?", (pid,)).fetchone()
        return json.loads(row["data"]) if row else {}

    # ------------------------------------------------------------------ 消息史

    def append_message(self, pid: str, role: str, content: str, meta: dict | None = None) -> None:
        """追加一条到全量史（messages 表，单轨真源）；窗口由 get_state 现算，无需回写。"""
        self._require(pid)
        now = _now()
        with self._connect() as conn:
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
