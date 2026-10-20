"""
田野罗盘 Agent — 配置管理
面向人类学/社会学田野调查者的旅行规划
本地优先存储，可选云同步
"""
import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".fieldwork_agent"
PROJECTS_DIR = CONFIG_DIR / "projects"

# 模型三槽默认值（server.py 请求级 LLMClient 重建在 cfg 缺席时也引用；单一来源）
DEFAULT_LLM_MODELS: dict[str, str] = {
    "interview": "qwen3.8-flash",
    "proposal": "qwen-max",
    "extract": "qwen3.8-flash",
}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"环境变量 {name}={raw!r} 不是合法整数") from None


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

        # 高德开放平台 Web 服务 Key（amap PoC）：env → 自有 config.json 覆盖
        self.amap_api_key: str = os.getenv("AMAP_API_KEY", "")

        # 高德 JS API Key + 安全密钥（PoC2 N6 Web 地图面板）：与 Web 服务 Key
        # 分开申请（控制台「Web 端(JS API)」类型）；防滥用靠域名白名单。
        self.amap_js_key: str = os.getenv("AMAP_JS_KEY", "")
        self.amap_js_security: str = os.getenv("AMAP_JS_SECURITY", "")

        # 应用访问令牌（审计 F02）：非空时 Web 宿主中间件要求 X-App-Token 匹配。
        # 仅环境变量——不进 save()/_load_config（令牌属部署边界，非用户偏好，不落盘）。
        self.app_token: str = os.getenv("FIELDWORK_APP_TOKEN", "")

        # 公网绑定声明（S01）：为真时启动期硬性要求 app_token 非空。
        # create_app 看不到 uvicorn 的真实 host，故由部署物显式声明——systemd
        # 对外监听即设 FIELDWORK_PUBLIC_BIND=1。默认关（本地开发/测试零影响），
        # 假值集合风格同 registration_open（空串/0/false/no/off 均视为关）。
        self.public_bind: bool = os.getenv(
            "FIELDWORK_PUBLIC_BIND", "0").strip().lower() not in (
                "0", "false", "no", "off", "")

        # 百炼套餐类型（coding/token）：套餐 Key（sk-sp-）配专属 OpenAI 兼容端点。
        # 仅环境变量——同 app_token 先例：服务端全局默认，页面回合级 header 可覆盖。
        self.dashscope_plan: str = os.getenv("FA_DASHSCOPE_PLAN", "")

        # 模型分档三槽：对话推进 / 长文方案 / 结构化抽取
        self.interview_model: str = os.getenv("FA_INTERVIEW_MODEL", DEFAULT_LLM_MODELS["interview"])
        self.proposal_model: str = os.getenv("FA_PROPOSAL_MODEL", DEFAULT_LLM_MODELS["proposal"])
        self.extract_model: str = os.getenv("FA_EXTRACT_MODEL", DEFAULT_LLM_MODELS["extract"])

        # 存储后端（spec §5.1 里程碑2：FA_STORAGE=sqlite 切换，默认 json）
        self.storage_backend: str = os.getenv("FA_STORAGE", "json").lower()
        self.sqlite_path: Path = CONFIG_DIR / "fieldwork.db"

        # 账号体系（第一期）：账号存储目录 + 会话/口令策略 + 注册开关
        # R1：默认取 projects_dir 的兄弟目录，绝不在 projects_dir 子树内
        self.user_store_dir: Path = Path(
            os.getenv("FA_USER_STORE_DIR", str(CONFIG_DIR / "accounts")))
        self.session_ttl_days: int = _env_int("FA_SESSION_TTL_DAYS", 30)
        self.pbkdf2_iterations: int = _env_int("FA_PBKDF2_ITERATIONS", 600000)
        # 注册开关是安全边界：归一化（strip+lower，同 FA_STORAGE 风格）后按常见
        # 假值集合判定，避免 FA_REGISTRATION_OPEN=false/no/off 被静默当成"开"。
        self.registration_open: bool = os.getenv(
            "FA_REGISTRATION_OPEN", "1").strip().lower() not in ("0", "false", "no", "off", "")

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
                with open(self.config_file, encoding="utf-8") as f:
                    data = json.load(f)
                self.dashscope_api_key = data.get("dashscope_api_key", self.dashscope_api_key)
                self.flyai_api_key = data.get("flyai_api_key", self.flyai_api_key)
                self.amap_api_key = data.get("amap_api_key", self.amap_api_key)
                self.amap_js_key = data.get("amap_js_key", self.amap_js_key)
                self.amap_js_security = data.get("amap_js_security", self.amap_js_security)
                self.dingtalk_webhook = data.get("dingtalk_webhook", self.dingtalk_webhook)
                self.dingtalk_enabled = data.get("dingtalk_enabled", self.dingtalk_enabled)
                self.interview_model = data.get("interview_model", self.interview_model)
                self.proposal_model = data.get("proposal_model", self.proposal_model)
                self.extract_model = data.get("extract_model", self.extract_model)
            except (OSError, json.JSONDecodeError):
                pass

        # 从 flyai CLI 配置文件读取 API Key
        flyai_config = Path.home() / ".flyai" / "config.json"
        if flyai_config.exists():
            try:
                with open(flyai_config, encoding="utf-8") as f:
                    flyai_data = json.load(f)
                stored_key = flyai_data.get("key", "")
                if stored_key and not self.flyai_api_key:
                    self.flyai_api_key = stored_key
            except (OSError, json.JSONDecodeError):
                pass

    def save(self):
        data = {
            "dashscope_api_key": self.dashscope_api_key,
            "flyai_api_key": self.flyai_api_key,
            "amap_api_key": self.amap_api_key,
            "amap_js_key": self.amap_js_key,
            "amap_js_security": self.amap_js_security,
            "dingtalk_webhook": self.dingtalk_webhook,
            "dingtalk_enabled": self.dingtalk_enabled,
            "interview_model": self.interview_model,
            "proposal_model": self.proposal_model,
            "extract_model": self.extract_model,
        }
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @property
    def llm_models(self) -> dict[str, str]:
        """三槽模型映射，直接用于 kernel.llm.LLMClient(models=...)。"""
        return {
            "interview": self.interview_model,
            "proposal": self.proposal_model,
            "extract": self.extract_model,
        }

    @property
    def dashscope_available(self) -> bool:
        return bool(self.dashscope_api_key)

    @property
    def flyai_available(self) -> bool:
        return bool(self.flyai_api_key)

    @property
    def amap_available(self) -> bool:
        return bool(self.amap_api_key)

    @property
    def amap_js_available(self) -> bool:
        return bool(self.amap_js_key)

    @property
    def dingtalk_available(self) -> bool:
        return bool(self.dingtalk_webhook) and self.dingtalk_enabled

    def get_project_dir(self, project_id: str) -> Path:
        pdir = self.projects_dir / project_id
        pdir.mkdir(parents=True, exist_ok=True)
        return pdir
