"""回合级研究上下文 patch 暂存（F05：取消即丢弃本轮未提交修改）。

web 回合把 TurnPatchBuffer 注入状态工具：写操作先进暂存区，轮末正常
结束统一 commit 落盘；取消/失败路径 discard，项目状态不受残缺回合影响。
staged_view 供 sanitize 的 NA 存量豁免读到"仓储+暂存"合并视图。
"""
from __future__ import annotations

from typing import Any


class TurnPatchBuffer:
    def __init__(self, repo: Any, pid: str) -> None:
        self._repo = repo
        self._pid = pid
        self._staged: dict[str, Any] = {}

    def staged_view(self) -> dict:
        """仓储当前值 + 本轮暂存 patch 的合并视图（浅合并，暂存键覆盖）。"""
        return {**self._repo.get_research_context(self._pid), **self._staged}

    def stage(self, patch: dict) -> None:
        self._staged.update(patch)

    def commit(self) -> int:
        """非空暂存整体写仓储并清空；返回写入的字段数（空暂存返 0）。"""
        if not self._staged:
            return 0
        self._repo.update_research_context(self._pid, dict(self._staged))
        n = len(self._staged)
        self._staged.clear()
        return n

    def discard(self) -> None:
        self._staged.clear()
