# tests/test_user_account_config.py
from pathlib import Path

import fieldwork_agent.kernel.config as cfgmod
from fieldwork_agent.kernel.config import Config

_FA_ACCOUNT_VARS = (
    "FA_USER_STORE_DIR",
    "FA_SESSION_TTL_DAYS",
    "FA_PBKDF2_ITERATIONS",
    "FA_REGISTRATION_OPEN",
)


def _isolate_config(monkeypatch, tmp_path):
    """把 Path.home / CONFIG_DIR / PROJECTS_DIR 指到 tmp_path，并清掉 FA_* 账号变量。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / ".fieldwork_agent")
    monkeypatch.setattr(cfgmod, "PROJECTS_DIR", tmp_path / ".fieldwork_agent" / "projects")
    for v in _FA_ACCOUNT_VARS:
        monkeypatch.delenv(v, raising=False)


def test_config_account_fields_defaults(monkeypatch, tmp_path):
    _isolate_config(monkeypatch, tmp_path)
    c = Config()
    assert c.session_ttl_days == 30
    assert c.pbkdf2_iterations == 600_000
    assert c.registration_open is True
    # R1：账号目录是 projects_dir 的兄弟，绝不在其子树内
    assert c.user_store_dir.name == "accounts"
    assert not c.user_store_dir.is_relative_to(c.projects_dir.resolve())


def test_registration_open_env_normalization(monkeypatch, tmp_path):
    """安全边界：常见假值（false/0/no/off/空串）必须关注册，真值必须开。"""
    _isolate_config(monkeypatch, tmp_path)
    for raw in ("false", "0", "", " FALSE ", "no", "off"):
        monkeypatch.setenv("FA_REGISTRATION_OPEN", raw)
        assert Config().registration_open is False, f"FA_REGISTRATION_OPEN={raw!r} 应关注册"
    for raw in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("FA_REGISTRATION_OPEN", raw)
        assert Config().registration_open is True, f"FA_REGISTRATION_OPEN={raw!r} 应开注册"


def test_fakeconfig_mirrors_account_fields(fake_config):
    assert fake_config.session_ttl_days == 30
    assert fake_config.pbkdf2_iterations == 600_000
    assert fake_config.registration_open is True
    assert fake_config.user_store_dir == fake_config.root / "accounts"
