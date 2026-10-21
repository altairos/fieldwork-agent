"""回合级状态变更暂存（F05：取消即丢弃本轮未提交修改）。

web/CLI 回合把 TurnPatchBuffer 注入状态工具：研究上下文 patch 与访谈对象
登记都先进暂存区，轮末正常结束统一 commit 落盘；取消/失败路径 discard，
项目状态不受残缺回合影响。staged_view 供 sanitize 的 NA 存量豁免读到
“仓储+暂存”合并视图。
"""
from __future__ import annotations

from typing import Any


class TurnPatchBuffer:
    def __init__(self, repo: Any, pid: str) -> None:
        self._repo = repo
        self._pid = pid
        self._staged: dict[str, Any] = {}
        # P0-2：save_informants 的待登记记录——与 ctx patch 同生命周期
        self._informants: list[dict] = []

    def staged_view(self) -> dict:
        """仓储当前值 + 本轮暂存 patch 的合并视图（浅合并，暂存键覆盖）。"""
        return {**self._repo.get_research_context(self._pid), **self._staged}

    def stage(self, patch: dict) -> None:
        self._staged.update(patch)

    def stage_informant(self, record: dict) -> None:
        """暂存一条待登记访谈对象（轮末 commit 时经 repo.save_informant 落盘）。"""
        self._informants.append(dict(record))

    def staged_informants(self) -> list[dict]:
        """本轮暂存的访谈对象记录（供编号/去重判定）。"""
        return list(self._informants)

    def commit(self) -> int:
        """非空暂存整体写仓储并清空；返回写入的研究上下文字段数（空暂存返 0）。

        访谈对象逐条写入（与上下文同一提交边界）；任一步失败向上抛，由轮末
        持久化块折为 error+failed，绝不静默半写。
        """
        for record in self._informants:
            self._repo.save_informant(self._pid, record)
        self._informants.clear()
        if not self._staged:
            return 0
        self._repo.update_research_context(self._pid, dict(self._staged))
        n = len(self._staged)
        self._staged.clear()
        return n

    def discard(self) -> None:
        self._staged.clear()
        self._informants.clear()
