"""Config 的磁盘副作用与密钥文件权限（审计 Q04 / S05）。"""

import shutil

from fieldwork_agent.core import config as config_mod
from fieldwork_agent.core.config import Config


def _patch_dirs(monkeypatch, tmp_path):
    """把 CONFIG_DIR / PROJECTS_DIR 指到 tmp_path 下（同 test_llm_config 手法）。"""
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(config_mod, "PROJECTS_DIR", tmp_path / "cfg" / "projects")


# ---------------------------------------------------------------- Q04 构造期无副作用


def test_construct_does_not_touch_disk(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    cfg = Config()
    assert cfg.projects_dir == tmp_path / "cfg" / "projects"
    assert not (tmp_path / "cfg").exists()


def test_get_project_dir_is_pure(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    cfg = Config()
    cfg.ensure_dirs()
    pdir = cfg.get_project_dir("p1")
    assert pdir == cfg.projects_dir / "p1"
    assert not pdir.exists()   # 只算路径，不建目录（落盘由仓储负责）


def test_ensure_dirs_is_idempotent(tmp_path, monkeypatch):
    _patch_dirs(monkeypatch, tmp_path)
    cfg = Config()
    cfg.ensure_dirs()
    cfg.ensure_dirs()
    assert (tmp_path / "cfg").is_dir()
    assert (tmp_path / "cfg" / "projects").is_dir()


def test_save_recreates_missing_config_dir(tmp_path, monkeypatch):
    """组合根未调 ensure_dirs 也不能让 save 失败（save 自己补建配置目录）。"""
    _patch_dirs(monkeypatch, tmp_path)
    cfg = Config()
    cfg.ensure_dirs()
    shutil.rmtree(tmp_path / "cfg")
    cfg.save()
    assert (tmp_path / "cfg" / "config.json").is_file()
