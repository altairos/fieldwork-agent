# tests/test_sqlite_user_store.py
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
