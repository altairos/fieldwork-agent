# tests/test_crypto.py
import threading
import time

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

    def bump():
        try:
            with FileLock(lock_path):
                cur = counter["n"]
                time.sleep(0.01)           # 放大竞争窗口 (M-6)：无锁时读/写间必然交错
                counter["n"] = cur + 1     # 临界区：无锁会丢更新
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=bump) for _ in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors and counter["n"] == 20
