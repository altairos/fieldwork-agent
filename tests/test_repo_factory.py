from fieldwork_agent.kernel.repository.factory import make_repository
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.kernel.repository.sqlite_repo import SqliteRepository


class _Cfg:
    def __init__(self, tmp_path, backend):
        self.storage_backend = backend
        self.projects_dir = tmp_path / "projects"
        self.sqlite_path = tmp_path / "fieldwork.db"


def test_default_is_json(tmp_path):
    repo = make_repository(_Cfg(tmp_path, "json"))
    assert isinstance(repo, JsonRepository)


def test_sqlite_switch(tmp_path):
    repo = make_repository(_Cfg(tmp_path, "sqlite"))
    assert isinstance(repo, SqliteRepository)


def test_unknown_backend_falls_back_json(tmp_path):
    repo = make_repository(_Cfg(tmp_path, "weird"))
    assert isinstance(repo, JsonRepository)


def test_sqlite_backend_functional(tmp_path):
    repo = make_repository(_Cfg(tmp_path, "sqlite"))
    repo.create("p1")
    repo.set_phase("p1", "field_site_profiles")
    assert make_repository(_Cfg(tmp_path, "sqlite")).get_phase("p1") == "field_site_profiles"
