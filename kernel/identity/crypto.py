# kernel/identity/crypto.py
"""账号口令/会话令牌的 stdlib 加密原语 + 跨进程文件锁（无新依赖）。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sys
import time
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
    """跨进程独占锁（msvcrt/fcntl）；锁一个旁路 .lock 文件，with 语义。

    timeout=None 无限等待（向后兼容）；正数=截止秒数，超时抛 TimeoutError。
    锁竞争错误重试（间隔 10ms）；POSIX 侧非竞争 OSError 立即上抛（不吞真实错误）。
    """

    _RETRY_INTERVAL = 0.01

    def __init__(self, path: Path | str, timeout: float | None = None):
        self._path = Path(path)
        self._timeout = timeout
        self._fh = None

    @staticmethod
    def _is_contention(exc: OSError) -> bool:
        if sys.platform == "win32":
            return True           # msvcrt LK_NBLCK 失败即视为锁竞争（EACCES/EDEADLK）
        return isinstance(exc, (BlockingIOError, PermissionError))

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "a+")
        try:
            self._acquire()
        except BaseException:
            self._fh.close()
            self._fh = None
            raise
        return self

    def _acquire(self) -> None:
        deadline = None if self._timeout is None else time.monotonic() + self._timeout
        while True:
            try:
                self._fh.seek(0)                     # 锁第 1 字节；与 __exit__ 解锁位置一致
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError as e:
                if not self._is_contention(e):
                    raise                            # 非竞争的真实错误：立即上抛
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError(f"文件锁等待超时: {self._path}") from e
                time.sleep(self._RETRY_INTERVAL)

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
