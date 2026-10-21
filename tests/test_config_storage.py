"""Config 的磁盘副作用与密钥文件权限（审计 Q04 / S05）。"""

import os
import shutil
import stat

from fieldwork_agent.kernel import config as config_mod
from fieldwork_agent.kernel.config import Config


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


# ---------------------------------------------------------------- S05 密钥文件权限


def test_save_hardens_secret_file_permissions(tmp_path, monkeypatch):
    """S05：含 API Key 的 config.json 落盘后收紧权限（POSIX 600 / Windows 去继承）。"""
    _patch_dirs(monkeypatch, tmp_path)
    cfg = Config()
    cfg.ensure_dirs()
    if os.name == "nt":
        calls = []
        monkeypatch.setattr(config_mod.subprocess, "run",
                            lambda *a, **kw: calls.append(a))
        cfg.save()
        assert calls, "Windows 应调用 icacls 收紧 ACL"
        argv = calls[0][0]
        assert argv[0].lower() == "icacls" and "/inheritance:r" in argv
    else:
        cfg.save()
        mode = stat.S_IMODE((tmp_path / "cfg" / "config.json").stat().st_mode)
        assert mode == 0o600


def test_restrict_permissions_is_best_effort(tmp_path):
    """权限收紧失败/文件不存在不得地雷式抛错（保存密钥是主操作）。"""
    config_mod._restrict_permissions(tmp_path / "missing.json")
