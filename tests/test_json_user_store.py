# tests/test_json_user_store.py
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
