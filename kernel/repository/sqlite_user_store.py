# kernel/repository/sqlite_user_store.py
"""SqliteUserStore — 与项目同库的 users/sessions 存储（FA_STORAGE=sqlite）。"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fieldwork_agent.kernel.identity.crypto import (
    hash_password,
    hash_token,
    new_session_token,
    verify_password,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY, email_norm TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL, salt TEXT NOT NULL,
    iterations INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


def _now() -> str:
    return datetime.now().isoformat()


def _norm(email: str) -> str:
    return (email or "").strip().lower()


class SqliteUserStore:
    def __init__(self, db_path: Path | str, *, pbkdf2_iterations: int = 600_000,
                 session_ttl_days: int = 30):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._iterations = pbkdf2_iterations
        self._ttl_days = session_ttl_days
        with self._connect() as c:
            c.executescript(_SCHEMA)          # 自持建表，与 SqliteRepository 无先后依赖

    @contextmanager
    def _connect(self, immediate: bool = False):
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

    @staticmethod
    def _public(r) -> dict:
        return {"user_id": r["user_id"], "email": r["email_norm"], "created_at": r["created_at"]}

    def create_user(self, email: str, password: str) -> dict:
        if not password or len(password) < 8:
            raise ValueError("密码至少 8 位")
        email_norm = _norm(email)
        if "@" not in email_norm:
            raise ValueError("非法邮箱")
        user_id = "u_" + uuid.uuid4().hex
        h, s, it = hash_password(password, iterations=self._iterations)
        now = _now()
        with self._connect(immediate=True) as c:
            if c.execute("SELECT 1 FROM users WHERE email_norm=?", (email_norm,)).fetchone():
                raise ValueError("邮箱已注册")
            c.execute("INSERT INTO users(user_id,email_norm,password_hash,salt,iterations,created_at)"
                      " VALUES(?,?,?,?,?,?)", (user_id, email_norm, h, s, it, now))
        return {"user_id": user_id, "email": email_norm, "created_at": now}

    def get_user_by_email(self, email: str) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM users WHERE email_norm=?", (_norm(email),)).fetchone()
        return self._public(r) if r else None

    def get_user(self, user_id: str) -> dict | None:
        with self._connect() as c:
            r = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return self._public(r) if r else None

    def verify_password(self, user_id: str, password: str) -> bool:
        with self._connect() as c:
            r = c.execute("SELECT password_hash,salt,iterations FROM users WHERE user_id=?",
                          (user_id,)).fetchone()
        return bool(r) and verify_password(password, r["password_hash"], r["salt"], r["iterations"])

    def create_session(self, user_id: str, ttl_days: int) -> str:
        raw = new_session_token()
        now = datetime.now()
        exp = (now + timedelta(days=ttl_days)).isoformat()
        with self._connect(immediate=True) as c:
            if not c.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone():
                raise KeyError(f"未知用户: {user_id}")
            c.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at,last_seen_at)"
                      " VALUES(?,?,?,?,?)", (hash_token(raw), user_id, now.isoformat(), exp,
                                            now.isoformat()))
        return raw

    def resolve_session(self, raw_token: str) -> str | None:
        if not raw_token.startswith("sess_"):
            return None
        th = hash_token(raw_token)
        now = datetime.now()
        with self._connect(immediate=True) as c:      # 滑动续期是写：immediate 串行化（R4）
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
            r = c.execute("SELECT user_id FROM sessions WHERE token_hash=?", (th,)).fetchone()
            if r is None:
                return None
            exp = (now + timedelta(days=self._ttl_days)).isoformat()
            c.execute("UPDATE sessions SET last_seen_at=?, expires_at=? WHERE token_hash=?",
                      (now.isoformat(), exp, th))
            return r["user_id"]

    def revoke_session(self, raw_token: str) -> None:
        with self._connect(immediate=True) as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (hash_token(raw_token),))
