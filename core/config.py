"""
田野罗盘 Agent — 配置管理
面向人类学/社会学田野调查者的旅行规划
本地优先存储，可选云同步
"""
import json
import os
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".fieldwork_agent"
PROJECTS_DIR = CONFIG_DIR / "projects"


class Config:
    """全局配置"""

    def __init__(self):
        self.config_dir = CONFIG_DIR
        self.projects_dir = PROJECTS_DIR
        self.config_file = CONFIG_DIR / "config.json"

        # 百炼 / DashScope
        self.dashscope_api_key: str = os.getenv("DASHSCOPE_API_KEY", "")
        self.dashscope_base_url: str = os.getenv(
            "DASHSCOPE_HTTP_BASE_URL", "https://dashscope.aliyuncs.com/api/v1"
        )

        # FlyAI（自动从 CLI 配置文件读取）
        self.flyai_api_key: str = os.getenv("FLYAI_API_KEY", "")

        # 模型选择
        self.chat_model: str = os.getenv("FA_CHAT_MODEL", "qwen-max")
        self.system_model: str = os.getenv("FA_SYSTEM_MODEL", "qwen-turbo")  # 用于结构化任务

        # 云同步（钉钉）
        self.dingtalk_webhook: str = ""
        self.dingtalk_enabled: bool = False

        self._ensure_dirs()
        self._load_config()

    def _ensure_dirs(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def _load_config(self):
        # 从自有配置文件加载
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.dashscope_api_key = data.get("dashscope_api_key", self.dashscope_api_key)
                self.flyai_api_key = data.get("flyai_api_key", self.flyai_api_key)
                self.dingtalk_webhook = data.get("dingtalk_webhook", self.dingtalk_webhook)
                self.dingtalk_enabled = data.get("dingtalk_enabled", self.dingtalk_enabled)
                self.chat_model = data.get("chat_model", self.chat_model)
                self.system_model = data.get("system_model", self.system_model)
            except (json.JSONDecodeError, IOError):
                pass

        # 从 flyai CLI 配置文件读取 API Key
        flyai_config = Path.home() / ".flyai" / "config.json"
        if flyai_config.exists():
            try:
                with open(flyai_config, "r", encoding="utf-8") as f:
                    flyai_data = json.load(f)
                stored_key = flyai_data.get("key", "")
                if stored_key and not self.flyai_api_key:
                    self.flyai_api_key = stored_key
            except (json.JSONDecodeError, IOError):
                pass

    def save(self):
        data = {
            "dashscope_api_key": self.dashscope_api_key,
            "flyai_api_key": self.flyai_api_key,
            "dingtalk_webhook": self.dingtalk_webhook,
            "dingtalk_enabled": self.dingtalk_enabled,
            "chat_model": self.chat_model,
            "system_model": self.system_model,
        }
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @property
    def dashscope_available(self) -> bool:
        return bool(self.dashscope_api_key)

    @property
    def flyai_available(self) -> bool:
        return bool(self.flyai_api_key)

    @property
    def dingtalk_available(self) -> bool:
        return bool(self.dingtalk_webhook) and self.dingtalk_enabled

    def get_project_dir(self, project_id: str) -> Path:
        pdir = self.projects_dir / project_id
        pdir.mkdir(parents=True, exist_ok=True)
        return pdir
