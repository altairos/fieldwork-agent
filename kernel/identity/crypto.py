# kernel/identity/crypto.py
"""账号口令/会话令牌的 stdlib 加密原语 + 跨进程文件锁（无新依赖）。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sys
from pathlib import Path


def hash_password(pw: str, *, iterations: int, salt: bytes | None = None
                  ) -> tuple[str, str, int]:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)
    return dk.hex(), salt.hex(), iterations


def verify_password(pw: str, hash_hex: str, salt_hex: str, iterations: int) -> bool:
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"),
                             bytes.fromhex(salt_hex), iterations)
    return hmac.compare_digest(dk.hex(), hash_hex)


def new_session_token() -> str:
    return "sess_" + secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class FileLock:
    """跨进程独占锁（msvcrt/fcntl）；锁一个旁路 .lock 文件，with 语义。"""

    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._fh = None

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a+")
        if sys.platform == "win32":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    pass
        else:
            import fcntl
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None
        return False
