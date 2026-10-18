"""pytest 共享 fixture。

用 FakeConfig 把项目状态目录指向 tmp_path，避免测试读写真实的
`~/.fieldwork_agent`，保证隔离与可重复。另提供宿主测试复用的
``fake_llm``（ScriptedLLM 形状）与 ``fake_flyai``（StubClient 形状）——
全 mock、不联网、不调 subprocess、无 key。
"""
from pathlib import Path

import pytest
from fieldwork_agent.kernel.models import Envelope, ItemPage, SearchItem


class FakeConfig:
    """最小可用的 Config 替身，仅提供内核宿主所需的目录与能力开关接口。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.projects_dir = self.root / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.dashscope_api_key = ""
        self.flyai_api_key = ""
        self.amap_api_key = ""   # 高德地理六原语（server/ orchestrator 组装直取）
        self.app_token = ""   # 审计 F02：App Token 守卫读取（空 = 不启用）
        self.dingtalk_webhook = ""
        self.dingtalk_enabled = False

    def get_project_dir(self, project_id: str) -> Path:
        pdir = self.projects_dir / project_id
        pdir.mkdir(parents=True, exist_ok=True)
        return pdir

    @property
    def dashscope_available(self) -> bool:
        return bool(self.dashscope_api_key)

    @property
    def flyai_available(self) -> bool:
        return bool(self.flyai_api_key)

    @property
    def dingtalk_available(self) -> bool:
        return bool(self.dingtalk_webhook) and self.dingtalk_enabled

    @property
    def llm_models(self) -> dict[str, str]:
        return {"interview": "fake-interview", "proposal": "fake-proposal",
                "extract": "fake-extract"}


@pytest.fixture
def fake_config(tmp_path) -> FakeConfig:
    return FakeConfig(tmp_path)


# ---------------------------------------------------------------- 宿主测试复用桩


class FakeLLM:
    """ScriptedLLM 形状：按脚本顺序返回 ``chat`` 响应（恒四键由脚本自备）。

    用法：``agent.llm = fake_llm([{"content": ..., "tool_calls": None}, ...])``；
    记录每次收到的 messages 到 ``calls``，供断言宿主组装的消息。
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append((list(messages), kw))
        return self.script.pop(0)


@pytest.fixture
def fake_llm():
    """返回 FakeLLM 类本身，测试用 ``fake_llm([...])`` 构造脚本化 LLM。"""
    return FakeLLM


class FakeFlyAI:
    """StubClient 形状：8 个搜索原语恒返回一条含单条目的成功包络，并记录调用。

    覆盖 profile_field_site/compose_itinerary 聚合内部对 client.* 的位置/关键字混合
    调用；``calls`` 记录 ``(方法名, kwargs)``（忽略位置参，够用于宿主透传断言）。
    """

    def __init__(self):
        self.calls = []

    def _env(self, name: str, kwargs: dict) -> Envelope:
        self.calls.append((name, dict(kwargs)))
        page = ItemPage(items=[SearchItem(name=f"{name}-结果A", price=120.0,
                                          price_raw="¥120")],
                        total=1, cheapest=120.0, dearest=120.0)
        return Envelope(ok=True, data=page)

    def __getattr__(self, method: str):
        def _stub(*args, **kwargs):
            return self._env(method, kwargs)

        return _stub


@pytest.fixture
def fake_flyai():
    """返回 FakeFlyAI 实例（StubClient 形状），供宿主透传/聚合断言复用。"""
    return FakeFlyAI()


@pytest.fixture(autouse=True)
def _public_assets(tmp_path, monkeypatch):
    """全部测试默认运行在 Tier P 公开版资产下：指向空目录，禁用本地
    repo/secrets 与 ~/.fieldwork_agent/assets 覆盖，避免私有包泄入测试。

    边界（终审 INFO-3，挂账）：本 fixture 只管测试**运行期**的资产访问；
    ``mcp_server/resources.STATIC_RESOURCES`` 在模块导入（collection）期
    构建、早于任何 fixture 生效——dev 机带 secrets/ 时该快照含私有词条，
    对 catalog 写形状断言前须先将其惰性化。
    """
    empty = tmp_path / "_empty_assets"
    empty.mkdir()
    monkeypatch.delenv("FIELDWORK_ASSETS_DIR", raising=False)
    from fieldwork_agent.kernel import assets
    assets.reload(empty)
    yield
    assets.reload(empty)


@pytest.fixture(autouse=True)
def _isolated_host_env(tmp_path, monkeypatch):
    """隔离部署边界环境：宿主侧变量不泄入任何测试，测试也不碰真实用户目录。

    根因（2026-10-18 web 401 批量失败排查）：共享开发 shell 里临时 export 的
    FIELDWORK_APP_TOKEN（部署验证用）被 pytest 子进程继承，config=None 的
    web 测试构造真实 Config() 读到令牌 → App Token 守卫拦掉全部 /projects
    系请求（70 例 401）。CI/部署流水线复用 runner 时同理会无差别崩。
    测试对部署边界变量必须不敏感：这里统一剥离，并仿 isolated_home 把
    CONFIG_DIR/PROJECTS_DIR/Path.home 指向 tmp_path——真实 ~/.fieldwork_agent
    （含 amap_api_key）与 ~/.flyai 不再被测试读写。
    """
    for var in ("FIELDWORK_APP_TOKEN", "DASHSCOPE_API_KEY",
                "DASHSCOPE_HTTP_BASE_URL", "FLYAI_API_KEY", "AMAP_API_KEY",
                "FA_DASHSCOPE_PLAN", "FA_INTERVIEW_MODEL",
                "FA_PROPOSAL_MODEL", "FA_EXTRACT_MODEL", "FA_STORAGE"):
        monkeypatch.delenv(var, raising=False)
    from fieldwork_agent.core import config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
