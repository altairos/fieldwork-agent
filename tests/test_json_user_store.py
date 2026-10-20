# tests/test_json_user_store.py
from datetime import datetime, timedelta

import pytest
from fieldwork_agent.kernel.repository.json_user_store import JsonUserStore


@pytest.fixture
def store(tmp_path):
    return JsonUserStore(tmp_path / "accounts", projects_dir=tmp_path / "projects",
                         pbkdf2_iterations=1000, session_ttl_days=30)


def test_r1_rejects_dir_under_projects(tmp_path):
    with pytest.raises(ValueError):
        JsonUserStore(tmp_path / "projects" / "_users", projects_dir=tmp_path / "projects")


def test_create_verify_session_roundtrip(store):
    u = store.create_user("a@b.com", "s3cret-pass")
    assert set(u) == {"user_id", "email", "created_at"}
    assert store.verify_password(u["user_id"], "s3cret-pass") is True
    tok = store.create_session(u["user_id"], 30)
    assert store.resolve_session(tok) == u["user_id"]
    store.revoke_session(tok)
    assert store.resolve_session(tok) is None


def test_duplicate_email_raises(store):
    store.create_user("a@b.com", "s3cret-pass")
    with pytest.raises(ValueError):
        store.create_user("a@b.com", "another-pass")


def test_resolve_session_hot_path_does_not_rewrite(store):
    """A05：半 TTL 内的连续解析不重写 sessions 文件；临近过期才续期一次。"""
    u = store.create_user("a@b.com", "s3cret-pass")
    tok = store.create_session(u["user_id"], 30)
    writes = []
    orig_write = store._write

    def counting(path, data):
        writes.append(path)
        return orig_write(path, data)

    store._write = counting
    for _ in range(100):
        assert store.resolve_session(tok) == u["user_id"]
    assert writes == []          # 新会话剩余 30 天 ≥ TTL/2：读路径不写

    sess = store._read(store._sessions)   # 压到 10 天后（< TTL/2）
    for rec in sess.values():
        rec["expires_at"] = (datetime.now() + timedelta(days=10)).isoformat()
    orig_write(store._sessions, sess)
    writes.clear()
    for _ in range(100):
        assert store.resolve_session(tok) == u["user_id"]
    assert len(writes) == 1      # 只续期一次，之后又回到零写

