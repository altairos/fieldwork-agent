# tests/test_web_public_bind_guard.py
"""S01：公网绑定必须配访问令牌（fail-closed）。

背景：`_app_token_ok` 在 `FIELDWORK_APP_TOKEN` 为空时恒返回 True——这是刻意的
本地开发友好设计，但只要部署物对外监听（systemd `--host 0.0.0.0`），
它就从"方便"变成"任何访问者都能读写项目并消耗服务端付费 LLM 额度"。
`create_app` 拿不到 uvicorn 的实际绑定地址，故由部署物显式声明
`FIELDWORK_PUBLIC_BIND=1`，启动期据此强制要求令牌。
"""
import pathlib

import pytest
from fieldwork_agent.kernel import config as config_mod
from fieldwork_agent.kernel.repository.json_repo import JsonRepository
from fieldwork_agent.web_host.app import create_app


def _repo(tmp_path):
    return JsonRepository(projects_dir=tmp_path / "projects")


def test_public_bind_without_token_refuses_to_start(tmp_path, fake_config):
    """对外监听 + 无令牌 = 裸奔：宁可不启动，也不静默开放。"""
    fake_config.public_bind = True
    fake_config.app_token = ""
    with pytest.raises(RuntimeError, match="FIELDWORK_APP_TOKEN"):
        create_app(config=fake_config, repo=_repo(tmp_path))


def test_public_bind_with_whitespace_only_token_refuses_to_start(tmp_path, fake_config):
    """env 文件里 `FIELDWORK_APP_TOKEN=   ` 很常见：纯空白等同于未配置。"""
    fake_config.public_bind = True
    fake_config.app_token = "   "
    with pytest.raises(RuntimeError, match="FIELDWORK_APP_TOKEN"):
        create_app(config=fake_config, repo=_repo(tmp_path))


def test_public_bind_with_token_starts_normally(tmp_path, fake_config):
    fake_config.public_bind = True
    fake_config.app_token = "t" * 32
    app = create_app(config=fake_config, repo=_repo(tmp_path))
    assert app.title == "Fieldwork Web Host"


def test_local_mode_without_token_still_starts(tmp_path, fake_config):
    """默认不变：未声明公网绑定时不因缺令牌拒绝启动（本地开发/测试常态）。"""
    assert getattr(fake_config, "public_bind", False) is False
    app = create_app(config=fake_config, repo=_repo(tmp_path))
    assert app.title == "Fieldwork Web Host"


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("off", False), ("", False),
])
def test_public_bind_env_parsing(isolated_dirs, monkeypatch, raw, expected):
    """假值集合与 registration_open 同风格：空串/0/false/no/off 一律视为关。"""
    monkeypatch.setenv("FIELDWORK_PUBLIC_BIND", raw)
    assert config_mod.Config().public_bind is expected


def test_public_bind_defaults_off(isolated_dirs, monkeypatch):
    monkeypatch.delenv("FIELDWORK_PUBLIC_BIND", raising=False)
    assert config_mod.Config().public_bind is False
