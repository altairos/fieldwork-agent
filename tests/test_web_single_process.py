"""A04：单进程硬约束——TurnManager 状态在进程内存，多 worker 必然分裂。"""

import pytest
from fieldwork_agent.kernel import config as config_mod
from fieldwork_agent.kernel.config import Config
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.app import create_app


def _repo(tmp_path):
    return JsonRepository(projects_dir=tmp_path / "projects")


def test_multi_worker_config_refuses_to_start(tmp_path, fake_config):
    fake_config.web_workers = 2
    with pytest.raises(RuntimeError, match="单进程"):
        create_app(config=fake_config, repo=_repo(tmp_path))


def test_single_worker_default_starts(tmp_path, fake_config):
    app = create_app(config=fake_config, repo=_repo(tmp_path))
    assert app.title == "Fieldwork Web Host"


def test_config_reads_worker_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(config_mod, "PROJECTS_DIR", tmp_path / "cfg" / "projects")
    for var in ("FA_WEB_WORKERS", "UVICORN_WORKERS", "WEB_CONCURRENCY"):
        monkeypatch.delenv(var, raising=False)
    assert Config().web_workers == 1
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    assert Config().web_workers == 2
    monkeypatch.setenv("FA_WEB_WORKERS", "4")
    assert Config().web_workers == 4   # 显式开关优先于通用变量
