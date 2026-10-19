# tests/test_user_account_config.py
from fieldwork_agent.core.config import Config, CONFIG_DIR


def test_config_account_fields_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(Path_home := __import__("pathlib").Path, "home",
                        classmethod(lambda cls: tmp_path))
    import fieldwork_agent.core.config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path / ".fieldwork_agent")
    monkeypatch.setattr(cfgmod, "PROJECTS_DIR", tmp_path / ".fieldwork_agent" / "projects")
    for v in ("FA_USER_STORE_DIR", "FA_SESSION_TTL_DAYS", "FA_PBKDF2_ITERATIONS",
              "FA_REGISTRATION_OPEN"):
        monkeypatch.delenv(v, raising=False)
    c = Config()
    assert c.session_ttl_days == 30
    assert c.pbkdf2_iterations == 600_000
    assert c.registration_open is True
    # R1：账号目录是 projects_dir 的兄弟，绝不在其子树内
    assert c.user_store_dir.name == "accounts"
    assert not c.user_store_dir.is_relative_to(c.projects_dir.resolve())


def test_fakeconfig_mirrors_account_fields(fake_config):
    assert fake_config.session_ttl_days == 30
    assert fake_config.pbkdf2_iterations == 600_000
    assert fake_config.registration_open is True
    assert fake_config.user_store_dir == fake_config.root / "accounts"
