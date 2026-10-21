"""兼容层（A09）：配置实现已下沉到 ``fieldwork_agent.kernel.config``。

历史原因 core 同时承担入口（cli/orchestrator）与底座（config），宿主
（web_host / mcp_server）依赖 ``core.config`` 形成反向边。实现迁到 kernel 后，
本模块只保留旧 import 路径给 CLI/脚本/第三方；新代码请直接使用
``from fieldwork_agent.kernel.config import Config``。

属性读取动态转发到 ``kernel.config``——运行期 patch ``kernel.config.CONFIG_DIR``
后，``core.config.CONFIG_DIR`` 会同步看到新值。但**写入不转发**：
``monkeypatch.setattr(core.config, "CONFIG_DIR", ...)`` 只会覆盖本模块的属性，
不影响实现。测试与运行时配置请直接 patch ``kernel.config``。
"""

from fieldwork_agent.kernel import config as _impl

__all__ = ["CONFIG_DIR", "DEFAULT_LLM_MODELS", "PROJECTS_DIR", "Config"]  # noqa: F822  （名称由 __getattr__ 转发）


def __getattr__(name: str):
    if name in __all__:
        return getattr(_impl, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
