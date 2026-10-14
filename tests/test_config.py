"""config — 密钥与模型的三级加载优先级、能力开关、保存。

优先级（与 config.py 实现一致）：
  * DashScope/模型：自有 config.json 覆盖环境变量
  * FlyAI：环境变量优先，缺失时回退 ~/.flyai/config.json
"""
import json
import os
import pathlib

import pytest
from fieldwork_agent.core import config as config_mod
from fieldwork_agent.core.config import Config


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """把 CONFIG_DIR / PROJECTS_DIR / Path.home 全部指向 tmp_path，并清空相关环境变量。"""
    projects = tmp_path / "projects"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "PROJECTS_DIR", projects)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    for var in ("DASHSCOPE_API_KEY", "FLYAI_API_KEY",
                "FA_INTERVIEW_MODEL", "FA_PROPOSAL_MODEL", "FA_EXTRACT_MODEL"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_defaults_isolated(isolated_home):
    cfg = Config()
    assert cfg.dashscope_api_key == ""
    assert cfg.flyai_api_key == ""
    assert cfg.interview_model == "qwen3.8-flash"
    assert cfg.proposal_model == "qwen-max"
    assert cfg.extract_model == "qwen3.8-flash"
    assert cfg.dashscope_available is False
    assert cfg.flyai_available is False
    assert cfg.dingtalk_available is False
    assert cfg.projects_dir.exists()


def test_env_reads_keys(isolated_home):
    os.environ["DASHSCOPE_API_KEY"] = "sk-env"
    os.environ["FA_PROPOSAL_MODEL"] = "qwen-plus"
    cfg = Config()
    assert cfg.dashscope_api_key == "sk-env"
    assert cfg.proposal_model == "qwen-plus"
    assert cfg.dashscope_available is True


def test_own_config_file_overrides_env(isolated_home):
    os.environ["DASHSCOPE_API_KEY"] = "sk-env"
    os.environ["FA_EXTRACT_MODEL"] = "env-extract"
    (isolated_home / "config.json").write_text(
        json.dumps({"dashscope_api_key": "sk-file", "extract_model": "file-extract"}),
        encoding="utf-8",
    )
    cfg = Config()
    # 自有配置文件优先于环境变量
    assert cfg.dashscope_api_key == "sk-file"
    assert cfg.extract_model == "file-extract"


def test_flyai_from_cli_config(isolated_home):
    flyai_dir = isolated_home / ".flyai"
    flyai_dir.mkdir()
    (flyai_dir / "config.json").write_text(json.dumps({"key": "fk-123"}), encoding="utf-8")
    cfg = Config()
    assert cfg.flyai_api_key == "fk-123"
    assert cfg.flyai_available is True


def test_flyai_env_wins_over_file(isolated_home):
    os.environ["FLYAI_API_KEY"] = "fk-env"
    flyai_dir = isolated_home / ".flyai"
    flyai_dir.mkdir()
    (flyai_dir / "config.json").write_text(json.dumps({"key": "fk-file"}), encoding="utf-8")
    cfg = Config()
    assert cfg.flyai_api_key == "fk-env"


def test_save_roundtrip(isolated_home):
    cfg = Config()
    cfg.dashscope_api_key = "sk-save"
    cfg.dingtalk_webhook = "https://hook"
    cfg.dingtalk_enabled = True
    cfg.extract_model = "qwen-save"
    cfg.save()
    reloaded = Config()
    assert reloaded.dashscope_api_key == "sk-save"
    assert reloaded.dingtalk_available is True
    # save()/_load_config() 的三槽键名同步（interview/proposal/extract_model）
    assert reloaded.extract_model == "qwen-save"
    saved = json.loads((isolated_home / "config.json").read_text(encoding="utf-8"))
    assert "chat_model" not in saved and "system_model" not in saved


def test_corrupt_config_file_ignored(isolated_home):
    (isolated_home / "config.json").write_text("{ not json", encoding="utf-8")
    cfg = Config()  # 不应抛异常，回落默认值
    assert cfg.dashscope_api_key == ""
    assert cfg.llm_models == {"interview": "qwen3.8-flash", "proposal": "qwen-max",
                              "extract": "qwen3.8-flash"}
