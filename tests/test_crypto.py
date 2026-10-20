# tests/test_crypto.py
import subprocess
import sys
import threading
import time
import types

import pytest
from fieldwork_agent.kernel.identity.crypto import (
    FileLock,
    hash_password,
    hash_token,
    new_session_token,
    verify_password,
)


def test_password_hash_roundtrip():
    h, s, it = hash_password("s3cret-pass", iterations=1000)
    assert it == 1000 and h and s
    assert verify_password("s3cret-pass", h, s, it) is True
    assert verify_password("wrong-pass", h, s, it) is False


def test_same_password_different_salt_different_hash():
    h1, s1, _ = hash_password("s3cret-pass", iterations=1000)
    h2, s2, _ = hash_password("s3cret-pass", iterations=1000)
    assert s1 != s2 and h1 != h2


def test_session_token_shape_and_hash():
    t = new_session_token()
    assert t.startswith("sess_") and len(t) < 128
    assert hash_token(t) == hash_token(t) and len(hash_token(t)) == 64
    # CSPRNG 契约：两次调用必须产生不同令牌 (M-8)
    assert new_session_token() != new_session_token()


def test_file_lock_serializes_writers(tmp_path):
    lock_path = tmp_path / "x.lock"
    counter = {"n": 0}
    errors = []
    n_threads = 5

    def bump():
        try:
            with FileLock(lock_path):
                cur = counter["n"]
                time.sleep(0.001)          # ~1ms GIL 释放，确保无锁时丢更新 (M-6)
                counter["n"] = cur + 1     # 临界区：无锁会丢更新
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=bump) for _ in range(n_threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors and counter["n"] == n_threads


def test_file_lock_cross_process_timeout_and_release(tmp_path):
    # M-7：真实子进程持锁 ~0.5s；父进程带 timeout 尝试 → TimeoutError；
    # 子进程释放后父进程能成功加锁。总耗时 <1s。
    lock_path = tmp_path / "cross.lock"
    child = (
        "import time\n"
        "from fieldwork_agent.kernel.identity.crypto import FileLock\n"
        f"with FileLock({str(lock_path)!r}):\n"
        "    print('LOCKED', flush=True)\n"
        "    time.sleep(0.5)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", child],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "LOCKED"   # 子进程已持锁
        with pytest.raises(TimeoutError):                   # 持锁期间：竞争超时
            with FileLock(lock_path, timeout=0.2):
                pass
    finally:
        proc.wait(timeout=10)
    with FileLock(lock_path, timeout=5.0):                   # 释放后：可成功加锁
        pass


def test_file_lock_posix_retries_contention_but_raises_real_errors(tmp_path, monkeypatch):
    # I-1：POSIX 分支 errno 区分——锁竞争(BlockingIOError/PermissionError)重试，
    # 非竞争 OSError 立即上抛（不吞真实错误）。
    calls = {"n": 0}

    def fake_flock(fd, flags):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise BlockingIOError()             # 锁竞争：应重试
        raise OSError("real error")             # 非竞争：应立即上抛

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "fcntl",
                        types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=fake_flock))
    with pytest.raises(OSError, match="real error"):
        with FileLock(tmp_path / "x.lock"):
            pass
    assert calls["n"] == 3                      # 2 次竞争重试后第 3 次真错误上抛
