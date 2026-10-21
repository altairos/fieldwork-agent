"""文献检索回合预算与重复查询守卫：每回合单独创建，线程安全，Web 并发工具与 CLI 共用。"""
from __future__ import annotations

import json
from collections.abc import Callable
from threading import Lock

from fieldwork_agent.kernel.models import Envelope, ToolError
from fieldwork_agent.kernel.sources.openalex import SearchLiteratureArgs
from pydantic import ValidationError


class DuplicateQueryGuard:
    """同回合完全重复的搜索请求直接拒绝（P2-c）。

    只挡“问法完全一样”的重复调用（空白归一、大小写不敏感），不设次数上限——
    次数仍由 ``SourceBudget`` / ``max_tool_calls`` 管。覆盖旅行关键词、AI 搜索、
    高德文本搜索与 POI 搜索；未覆盖的工具原样放行。

    P2-4：签名只在**成功返回后**记账（失败回滚）——网络/上游瞬时失败后同一
    查询必须可重试；否则提示「请使用已有结果」时手里根本没有结果。

    守卫只覆盖下列 4 个搜索原语，不含 ``search_literature``（后者由
    ``bind_research_searches`` 处理），故与包装先后无关。
    """

    _QUERY_FIELDS = {
        "search_keyword": ("query",),
        "search_ai": ("query",),
        "amap_text_search": ("keywords", "city"),
        "search_pois": ("city_name", "keyword"),
    }

    def __init__(self):
        self.seen: set[str] = set()
        self.lock = Lock()

    @staticmethod
    def _norm(value) -> str:
        return " ".join(str(value or "").split()).lower()

    def wrap(self, dispatch: Callable[[str, dict], Envelope]) -> Callable[[str, dict], Envelope]:
        def guarded(name: str, args: dict) -> Envelope:
            fields = self._QUERY_FIELDS.get(name)
            if not fields:
                return dispatch(name, args)
            parts = tuple(self._norm((args or {}).get(field)) for field in fields)
            if not any(parts):
                # 关键字段全空：交给参数校验去拒，去重在这里只会误拦。
                return dispatch(name, args)
            signature = name + "::" + "|".join(parts)
            with self.lock:
                if signature in self.seen:
                    return Envelope(ok=False, error=ToolError(
                        code="duplicate_query",
                        message="本回合已执行相同查询，该调用未执行",
                        hint="请使用已有结果，或改写查询条件（地点/类型/关键词）后再试。"))
                self.seen.add(signature)
            env = dispatch(name, args)
            if not env.ok:
                # P2-4：失败回滚签名——瞬时故障后同一查询仍可重试。
                with self.lock:
                    self.seen.discard(signature)
            return env
        return guarded


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
