"""文献检索回合预算：每个回合单独创建，线程安全，Web并发工具与CLI共用。"""
from __future__ import annotations

import json
from collections.abc import Callable
from threading import Lock

from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.kernel.sources.openalex import SearchLiteratureArgs
from pydantic import ValidationError


class LiteratureSearchBudget:
    """最多3次工具调用、8路请求和24篇返回名额；网络失败也计已消耗额度。"""

    def __init__(self):
        self.calls = 0
        self.requests = 0
        self.reserved_results = 0
        self.signatures: set[str] = set()
        self.lock = Lock()

    def wrap(self, dispatch: Callable[[str, dict], Envelope]) -> Callable[[str, dict], Envelope]:
        def bounded(name: str, args: dict) -> Envelope:
            if name != "search_literature":
                return dispatch(name, args)
            try:
                validated = SearchLiteratureArgs.model_validate(args)
            except ValidationError as exc:
                return Envelope(ok=False, error=ToolError(
                    code="invalid_arguments", message=str(exc), hint="按文献工具schema修正参数。"))
            normalized = validated.model_dump()
            # 返回条数不是新的检索策略；仅增加max_results不能绕过重复查询保护。
            signature = json.dumps({
                "queries": sorted({row.expression() for row in validated.search_matrix})
                or [validated.query], "stage": validated.stage,
                "search_field": validated.search_field, "year_from": validated.year_from,
                "year_to": validated.year_to, "open_access_only": validated.open_access_only,
            }, ensure_ascii=False, sort_keys=True)
            requested = len(validated.search_matrix) or 1
            with self.lock:
                if signature in self.signatures:
                    return self._stop("literature_duplicate_query", "本回合已执行相同检索，请使用已有结果或改写不足路线。")
                remaining = 24 - self.reserved_results
                if self.calls >= 3 or self.requests + requested > 8 or remaining <= 0:
                    return self._stop("literature_budget_exceeded", "本回合文献检索预算已用尽，请基于已有候选完成筛选。")
                normalized["max_results"] = min(validated.max_results, remaining)
                self.calls += 1
                self.requests += requested
                self.reserved_results += normalized["max_results"]
                self.signatures.add(signature)
            return dispatch(name, normalized)
        return bounded

    @staticmethod
    def _stop(code: str, message: str) -> Envelope:
        return Envelope(ok=False, error=ToolError(
            code=code, message=message,
            hint="停止重复扩检；只引用已返回的真实条目，说明范围与缺口，不虚构文献。"))
