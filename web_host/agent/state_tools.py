# web_host/agent/state_tools.py
"""Web 回合状态工具：实现已上移 runtime/state_tools.py（F02，web/CLI 共用）。

旧名 build_state_tools 保留 re-export，web loop 与既有测试 import 不变。
"""
from fieldwork_agent.runtime.state_tools import build_project_state_tools

build_state_tools = build_project_state_tools

__all__ = ["build_state_tools", "build_project_state_tools"]
