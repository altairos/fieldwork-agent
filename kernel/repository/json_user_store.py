# kernel/repository/json_user_store.py
"""JsonUserStore — 账号存储的 JSON 后端（FA_STORAGE=json）。

R1：user_store_dir 必须在 projects_dir 之外（否则 project_id="_users" 可被 delete_project rmtree）。
R4：users.json/sessions.json 是全局单文件，完整读改写走 FileLock（跨进程）+ 原子替换。
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fieldwork_agent.kernel.identity.crypto import (
    FileLock,
    hash_password,
    hash_token,
    new_session_token,
    verify_password,
)


def _now() -> str:
    return datetime.now().isoformat()


def _norm(email: str) -> str:
    return (email or "").strip().lower()


class JsonUserStore:
    def __init__(self, user_store_dir: Path | str, *, projects_dir: Path | str | None = None,
                 pbkdf2_iterations: int = 600_000, session_ttl_days: int = 30):
        self._dir = Path(user_store_dir)
        if projects_dir is not None:
            pd = Path(projects_dir).resolve()
            if self._dir.resolve().is_relative_to(pd):
                raise ValueError(f"user_store_dir 不得在 projects_dir 子树内（R1）: {self._dir}")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._iterations = pbkdf2_iterations
        self._ttl_days = session_ttl_days
        self._users = self._dir / "users.json"
        self._sessions = self._dir / "sessions.json"

    def _read(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"账号文件损坏: {path}") from e

    def _write(self, path: Path, data: dict) -> None:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())                  # 数据块先于 rename 落盘，断电不致变砖
        os.replace(tmp, path)                     # 原子替换

    def _locked(self, path: Path):
        return FileLock(path.with_suffix(".lock"), timeout=5.0)  # 镜像 sqlite busy_timeout=5000 量级

    @staticmethod
    def _public(rec: dict) -> dict:
        return {"user_id": rec["user_id"], "email": rec["email_norm"], "created_at": rec["created_at"]}

    def create_user(self, email: str, password: str) -> dict:
        if not password or len(password) < 8:
            raise ValueError("密码至少 8 位")
        email_norm = _norm(email)
        if "@" not in email_norm:
            raise ValueError("非法邮箱")
        user_id = "u_" + uuid.uuid4().hex
        h, s, it = hash_password(password, iterations=self._iterations)
        now = _now()
        with self._locked(self._users):
            users = self._read(self._users)
            if any(u["email_norm"] == email_norm for u in users.values()):
                raise ValueError("邮箱已注册")
            users[user_id] = {"user_id": user_id, "email_norm": email_norm,
                              "password_hash": h, "salt": s, "iterations": it, "created_at": now}
            self._write(self._users, users)
        return {"user_id": user_id, "email": email_norm, "created_at": now}

    def get_user_by_email(self, email: str) -> dict | None:
        en = _norm(email)
        with self._locked(self._users):
            for u in self._read(self._users).values():
                if u["email_norm"] == en:
                    return self._public(u)
        return None

    def get_user(self, user_id: str) -> dict | None:
        with self._locked(self._users):
            u = self._read(self._users).get(user_id)
        return self._public(u) if u else None

    def verify_password(self, user_id: str, password: str) -> bool:
        with self._locked(self._users):
            u = self._read(self._users).get(user_id)
        return bool(u) and verify_password(password, u["password_hash"], u["salt"], u["iterations"])

    def burn_password_check(self, password: str) -> None:
        """S03：为不存在的账号跑一次同强度哈希，结果丢弃。

        不这么做的话，“账号不存在”会在 PBKDF2 之前就返回，登录耗时直接成为
        邮箱枚举器（存在 ~几百毫秒、不存在 ~毫秒）。
        """
        hash_password(password or "", iterations=self._iterations)

    def create_session(self, user_id: str, ttl_days: int) -> str:
        raw = new_session_token()
        now = datetime.now()
        exp = (now + timedelta(days=ttl_days)).isoformat()
        with self._locked(self._sessions):
            sess = self._read(self._sessions)
            if not self.get_user(user_id):
                raise KeyError(f"未知用户: {user_id}")
            # A05：登录是低频写，顺手摊销清理过期会话（读路径不再全量重写）。
            sess = {k: v for k, v in sess.items() if v["expires_at"] >= now.isoformat()}
            sess[hash_token(raw)] = {"user_id": user_id, "created_at": now.isoformat(),
                                     "expires_at": exp, "last_seen_at": now.isoformat()}
            self._write(self._sessions, sess)
        return raw

    def resolve_session(self, raw_token: str) -> str | None:
        if not raw_token.startswith("sess_"):
            return None
        th = hash_token(raw_token)
        now = datetime.now()
        with self._locked(self._sessions):                 # R4：续期与撤销在同一锁内串行
            sess = self._read(self._sessions)
            rec = sess.get(th)
            # A05：读路径不重写文件（未命中时以前也会写一次，等于每请求一次全量落盘）。
            # 过期行由续期这条低频写路径摊销清理。
            if rec is None:
                return None
            if rec["expires_at"] < now.isoformat():
                sess.pop(th, None)
                self._write(self._sessions, sess)
                return None
            remaining = datetime.fromisoformat(rec["expires_at"]) - now
            if remaining >= timedelta(days=self._ttl_days / 2):
                return rec["user_id"]
            sess = {k: v for k, v in sess.items() if v["expires_at"] >= now.isoformat()}
            rec["last_seen_at"] = now.isoformat()
            rec["expires_at"] = (now + timedelta(days=self._ttl_days)).isoformat()
            self._write(self._sessions, sess)
            return rec["user_id"]

    def revoke_session(self, raw_token: str) -> None:
        with self._locked(self._sessions):
            sess = self._read(self._sessions)
            sess.pop(hash_token(raw_token), None)
            self._write(self._sessions, sess)
