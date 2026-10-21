"""A09：分层守卫——宿主（web_host / mcp_server）不得反向依赖 core。

core 同时装入口（cli/orchestrator）与底座（config），导致依赖图里出现
`mcp_server → core`、`web_host → core` 的反向边。Config 已下沉到
``kernel.config``；本用例把“宿主对 core 的 import 归零”钉成门禁。
"""

import ast
from pathlib import Path

import fieldwork_agent

ROOT = Path(fieldwork_agent.__file__).parent
HOST_PACKAGES = ("web_host", "mcp_server")


def _core_imports(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("fieldwork_agent.core"):
                lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("fieldwork_agent.core"):
                    lines.append(node.lineno)
    return lines


def test_hosts_do_not_import_core():
    offenders = []
    for package in HOST_PACKAGES:
        for path in (ROOT / package).rglob("*.py"):
            for line in _core_imports(path):
                rel = path.relative_to(ROOT).as_posix()
                offenders.append(f"{rel}:{line}")
    assert not offenders, "宿主包仍在 import core：" + ", ".join(offenders)


def test_legacy_core_config_reexports_kernel_objects():
    """兼容层必须是同一对象，不能是复制——复制会让 patch/常量悄悄分叉。"""
    from fieldwork_agent.core import config as legacy
    from fieldwork_agent.kernel import config as current

    assert legacy.Config is current.Config
    assert legacy.DEFAULT_LLM_MODELS is current.DEFAULT_LLM_MODELS
    assert legacy.CONFIG_DIR == current.CONFIG_DIR

