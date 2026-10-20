# tests/test_sqlite_user_store.py
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from fieldwork_agent.kernel.repository.sqlite_user_store import SqliteUserStore


@pytest.fixture
def store(tmp_path):
    return SqliteUserStore(tmp_path / "t.db", pbkdf2_iterations=1000, session_ttl_days=30)


def test_create_user_public_shape_and_email_norm(store):
    u = store.create_user("Alice@Example.com", "s3cret-pass")
    assert u["email"] == "alice@example.com" and u["user_id"].startswith("u_")
    assert set(u) == {"user_id", "email", "created_at"}          # 脱敏（无 hash/salt）
    assert store.get_user_by_email("ALICE@example.com")["user_id"] == u["user_id"]


def test_duplicate_email_raises(store):
    store.create_user("a@b.com", "s3cret-pass")
    with pytest.raises(ValueError):
        store.create_user("A@B.com", "another-pass")


def test_short_password_raises(store):
    with pytest.raises(ValueError):
        store.create_user("a@b.com", "short")


def test_verify_password(store):
    u = store.create_user("a@b.com", "s3cret-pass")
    assert store.verify_password(u["user_id"], "s3cret-pass") is True
    assert store.verify_password(u["user_id"], "wrong-pass") is False


def test_session_resolve_and_revoke(store):
    u = store.create_user("a@b.com", "s3cret-pass")
    tok = store.create_session(u["user_id"], 30)
    assert tok.startswith("sess_")
    assert store.resolve_session(tok) == u["user_id"]
    store.revoke_session(tok)
    assert store.resolve_session(tok) is None                     # 撤销后不复活


def test_expired_session_not_resolved(tmp_path):
    store = SqliteUserStore(tmp_path / "t.db", pbkdf2_iterations=1000)
    u = store.create_user("a@b.com", "s3cret-pass")
    tok = store.create_session(u["user_id"], ttl_days=-1)         # 立即过期
    assert store.resolve_session(tok) is None


def test_resolve_session_hot_path_is_read_only(store):
    """A05：半 TTL 内的连续解析零写事务；临近过期才续期一次。"""
    u = store.create_user("a@b.com", "s3cret-pass")
    tok = store.create_session(u["user_id"], 30)
    writes = []
    orig = store._connect

    @contextmanager
    def counting(immediate=False):
        if immediate:
            writes.append(1)
        with orig(immediate=immediate) as c:
            yield c

    store._connect = counting
    for _ in range(100):
        assert store.resolve_session(tok) == u["user_id"]
    assert writes == []          # 新会话剩余 30 天 ≥ TTL/2：读路径不写

    with orig(immediate=True) as c:   # 压到 10 天后（< TTL/2）
        c.execute("UPDATE sessions SET expires_at=? WHERE user_id=?",
                  ((datetime.now() + timedelta(days=10)).isoformat(), u["user_id"]))
    for _ in range(100):
        assert store.resolve_session(tok) == u["user_id"]
    assert len(writes) == 1      # 只续期一次，之后又回到零写

