# tests/test_user_store_factory.py
import pytest
from fieldwork_agent.kernel.repository.json_user_store import JsonUserStore
from fieldwork_agent.kernel.repository.sqlite_user_store import SqliteUserStore
from fieldwork_agent.kernel.repository.user_store_factory import make_user_store


class Cfg:
    def __init__(self, tmp, backend):
        self.storage_backend = backend
        self.projects_dir = tmp / "projects"
        self.user_store_dir = tmp / "accounts"
        self.sqlite_path = tmp / "fieldwork.db"
        self.pbkdf2_iterations = 600_000
        self.session_ttl_days = 30


def test_factory_json(tmp_path):
    assert isinstance(make_user_store(Cfg(tmp_path, "json")), JsonUserStore)


def test_factory_sqlite(tmp_path):
    assert isinstance(make_user_store(Cfg(tmp_path, "sqlite")), SqliteUserStore)


def test_factory_rejects_weak_kdf(tmp_path):
    c = Cfg(tmp_path, "json")
    c.pbkdf2_iterations = 999
    with pytest.raises(ValueError, match="below safety floor"):
        make_user_store(c)


def test_factory_forwards_policy(tmp_path):
    for backend in ("json", "sqlite"):
        c = Cfg(tmp_path, backend)
        c.pbkdf2_iterations, c.session_ttl_days = 123_456, 7
        s = make_user_store(c)
        assert s._iterations == 123_456
        assert s._ttl_days == 7


def test_factory_r1_rejects_accounts_under_projects(tmp_path):
    c = Cfg(tmp_path, "json")
    c.user_store_dir = tmp_path / "projects" / "_users"
    with pytest.raises(ValueError):
        make_user_store(c)
