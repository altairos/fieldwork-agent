# tests/test_user_store_contract.py
"""UserStore 契约：Sqlite/Json 双实现逐条一致（参数化双跑）。"""
import threading

import pytest
from fieldwork_agent.kernel.repository.json_user_store import JsonUserStore
from fieldwork_agent.kernel.repository.sqlite_user_store import SqliteUserStore


@pytest.fixture(params=["sqlite", "json"])
def store(request, tmp_path):
    if request.param == "sqlite":
        return SqliteUserStore(tmp_path / "t.db", pbkdf2_iterations=1000, session_ttl_days=30)
    return JsonUserStore(tmp_path / "accounts", projects_dir=tmp_path / "projects",
                         pbkdf2_iterations=1000, session_ttl_days=30)


def test_create_get_verify(store):
    u = store.create_user("a@b.com", "s3cret-pass")
    assert set(u) == {"user_id", "email", "created_at"}
    assert store.get_user(u["user_id"])["email"] == "a@b.com"
    assert store.get_user_by_email("A@B.com")["user_id"] == u["user_id"]
    assert store.verify_password(u["user_id"], "s3cret-pass") is True
    assert store.verify_password(u["user_id"], "nope-nope") is False


def test_duplicate_email(store):
    store.create_user("a@b.com", "s3cret-pass")
    with pytest.raises(ValueError):
        store.create_user("a@b.com", "another-pass")


def test_short_password(store):
    with pytest.raises(ValueError):
        store.create_user("a@b.com", "short")


def test_session_lifecycle(store):
    u = store.create_user("a@b.com", "s3cret-pass")
    tok = store.create_session(u["user_id"], 30)
    assert store.resolve_session(tok) == u["user_id"]
    store.revoke_session(tok)
    assert store.resolve_session(tok) is None


def test_expired_session(store):
    u = store.create_user("a@b.com", "s3cret-pass")
    assert store.resolve_session(store.create_session(u["user_id"], -1)) is None


def test_unknown_user_session_raises(store):
    with pytest.raises(KeyError):
        store.create_session("u_doesnotexist", 30)


def test_concurrent_renew_vs_revoke_ends_revoked(store):
    """R4：续期与撤销竞争，终态必须是撤销（旧快照不得复活已撤销会话）。"""
    u = store.create_user("a@b.com", "s3cret-pass")
    tok = store.create_session(u["user_id"], 30)
    barrier = threading.Barrier(2, timeout=5)

    def renew():
        barrier.wait()
        for _ in range(30):
            store.resolve_session(tok)

    def revoke():
        barrier.wait()
        store.revoke_session(tok)

    ts = [threading.Thread(target=renew), threading.Thread(target=revoke)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert store.resolve_session(tok) is None
