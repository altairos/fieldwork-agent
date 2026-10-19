"""两旅行源的实际请求预算、实例缓存和相同在途请求合并。"""
import functools
import hashlib
import json
import logging
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

from fieldwork_agent.kernel.models import Envelope, ItemPage, ToolError

_CURRENT: ContextVar = ContextVar("fieldwork_source_budget", default=None)


class SourceBudget:
    def __init__(self, limit: int = 20, namespace: str = "", cancelled=None, deadline: float | None = None):
        self.limit = limit
        self.namespace = namespace or uuid.uuid4().hex
        self.cancelled = cancelled
        self.deadline = deadline if deadline is not None else time.monotonic() + 60
        self.lock = threading.Lock()
        self.counts = {"actual_requests": 0, "cache_hits": 0, "coalesced": 0, "failed": 0, "blocked": 0}
        self.sources: dict[str, int] = {}
        self.disabled_sources: dict[str, ToolError] = {}

    def bump(self, name: str):
        with self.lock:
            self.counts[name] += 1

    def reserve(self, source: str) -> Envelope | None:
        with self.lock:
            if source in self.disabled_sources:
                self.counts["blocked"] += 1
                return Envelope(ok=False, error=self.disabled_sources[source].model_copy(deep=True), usage={"request_blocked": True})
            if time.monotonic() >= self.deadline or (self.cancelled and self.cancelled()) or self.counts["actual_requests"] >= self.limit:
                self.counts["blocked"] += 1
                return Envelope(ok=False, error=ToolError(code="source_budget_exceeded",
                    message="本任务旅行/地理请求预算已用尽或任务已停止", hint="交付已有结果及剩余缺口，不继续重复调用。"))
            self.counts["actual_requests"] += 1
            self.sources[source] = self.sources.get(source, 0) + 1
        return None

    def snapshot(self):
        with self.lock:
            return {**self.counts, "limit": self.limit, "by_source": dict(self.sources),
                    "disabled_sources": {source: error.code for source, error in self.disabled_sources.items()}}

    def disable_on_failure(self, env):
        code = env.error.code if env.error else ""
        if code in {"amap_ip_denied", "amap_invalid_key", "amap_quota", "flyai_rate_limited", "flyai_unavailable"}:
            with self.lock:
                self.disabled_sources["amap" if code.startswith("amap_") else "flyai"] = env.error.model_copy(deep=True)

    def wrap(self, fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            token = _CURRENT.set(self)
            try:
                value = fn(*args, **kwargs)
                usage = self.snapshot()
                if any(self.counts.values()):
                    logging.getLogger(__name__).info("travel_source_usage %s", json.dumps(usage, sort_keys=True))
                    if isinstance(value, Envelope):
                        value.usage = usage
                    elif isinstance(value, dict):
                        value = {**value, "usage": usage}
                return value
            finally:
                _CURRENT.reset(token)
        wrapped._source_budget = self
        return wrapped


def source_task(fn):
    """直接调用聚合或MCP单工具时有独立上限；CLI/Web传入的整轮预算优先。"""
    @functools.wraps(fn)
    def task(*args, **kwargs):
        if _CURRENT.get() is not None:
            return fn(*args, **kwargs)
        return SourceBudget().wrap(fn)(*args, **kwargs)
    return task


def request_guard(source: str, client) -> Envelope | None:
    budget = _CURRENT.get()
    blocked = budget.reserve(source) if budget else None
    if blocked:
        return blocked
    with client._cache_lock:
        client.stats["actual_requests"] = client.stats.get("actual_requests", 0) + 1
    return None


def cache_key(client, operation):
    budget = _CURRENT.get()
    identity = hashlib.sha256(str(client.api_key).encode()).hexdigest()
    def normalize(value):
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            return {key: normalize(v) for key, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(v) for v in value]
        return value
    return (identity, budget.namespace if budget else "direct", json.dumps(normalize(operation), sort_keys=True, default=str))


@dataclass
class Pending:
    event: threading.Event = field(default_factory=threading.Event)
    result: Envelope | None = None


def _reused(env: Envelope, *, cached: bool) -> Envelope:
    copied = env.model_copy(deep=True)
    evidence = [copied.evidence]
    if hasattr(copied.data, "evidence"):
        evidence.append(copied.data.evidence)
    if isinstance(copied.data, ItemPage):
        evidence.extend(i.evidence for i in copied.data.items)
    for item in evidence:
        if item:
            item.cache_hit = cached
            item.coalesced = not cached
    if isinstance(copied.data, list):
        for row in copied.data:
            if isinstance(row, dict) and isinstance(row.get("evidence"), dict):
                row["evidence"].update(cache_hit=cached, coalesced=not cached)
    return copied


def cached_request(client, key, ttl: float, fetch) -> Envelope:
    budget = _CURRENT.get()
    with client._cache_lock:
        hit = client._cache.get(key)
        if hit and hit[0] > time.monotonic():
            client.stats["cache_hit"] = client.stats.get("cache_hit", 0) + 1
            if budget:
                budget.bump("cache_hits")
            return _reused(hit[1], cached=True)
        pending = client._inflight.get(key)
        owner = pending is None
        if owner:
            pending = Pending()
            client._inflight[key] = pending
    if not owner:
        if budget:
            budget.bump("coalesced")
        if not pending.event.wait(timeout=40):
            return Envelope(ok=False, error=ToolError(code="source_wait_timeout", message="等待相同请求超时"))
        return _reused(pending.result, cached=False)
    start = time.monotonic()
    try:
        try:
            env = fetch()
            if not isinstance(env, Envelope):
                env = Envelope(ok=False, error=ToolError(code="source_bad_envelope", message="数据源返回形状无效"))
        except Exception as exc:
            env = Envelope(ok=False, error=ToolError(code="source_exception", message=str(exc)))
        if not env.ok and budget and not (env.usage or {}).get("request_blocked") and (not env.error or env.error.code not in ("source_budget_exceeded", "amap_invalid_key")):
            budget.bump("failed")
        if not env.ok and budget:
            budget.disable_on_failure(env)
        with client._cache_lock:
            client.stats["latency_ms"] = client.stats.get("latency_ms", 0) + round((time.monotonic() - start) * 1000)
            if env.ok:
                if len(client._cache) >= 256:
                    client._cache.pop(min(client._cache, key=lambda k: client._cache[k][0]))
                client._cache[key] = (time.monotonic() + ttl, env.model_copy(deep=True))
        pending.result = env.model_copy(deep=True)
        return env
    finally:
        with client._cache_lock:
            client._inflight.pop(key, None)
            pending.event.set()
