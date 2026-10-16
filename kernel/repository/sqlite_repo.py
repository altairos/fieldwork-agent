"""``SqliteRepository`` — ``ProjectRepository`` 的 SQLite 实现（spec §5 里程碑 2）。

与 JsonRepository 逐行为对齐（由 tests/repository_contract.py 双跑钉死）。要点：
- stdlib sqlite3、无 ORM；PRAGMA user_version 管 schema 版本；
- WAL + busy_timeout=5000 支持 MCP/Web 双进程并发读；
- 每次操作开新连接（context manager），实例可随时重建、无缓存；
- 记录类（田野点/报道人）以 JSON data 列存整条记录，保证 schemaless dict 平价。
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

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
