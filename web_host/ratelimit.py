"""S02：进程内限流原语（滑动窗口 + 日配额）。

部署边界：状态只在本进程内——当前单进程 uvicorn 成立；多 worker / 多实例
部署时各进程各算一份，需换共享后端（Redis 等）才能得到全局配额。

键来自外部输入（IP、邮箱、owner），因此容量必须有界（``max_keys``）：
否则限流器自身会成为内存放大器。拒绝**不记账**——持续撞墙不应让封锁
自我延长，合法用户过了窗口就该重新获得配额。
"""

import ipaddress
import math
import time

DEFAULT_LOGIN_LIMIT = 10          # 次/分钟/IP
DEFAULT_LOGIN_ACCOUNT_LIMIT = 10  # 次/分钟/账号
DEFAULT_REGISTER_LIMIT = 20       # 次/小时/IP
DEFAULT_TURN_DAILY_QUOTA = 200    # 次/天/owner


def client_ip(request) -> str:
    """请求方 IP：直连按 peer；仅当 peer 是环回/内网（nginx 反代）才信 X-Real-IP。

    公网直连 2143 的客户端可以自带任意 X-Real-IP——无条件采信等于把按 IP
    限流变成可选项。nginx 侧用 ``proxy_set_header X-Real-IP $remote_addr``
    覆盖写，二者配合成立；非 IP 的 peer（如 ASGI 测试端的 "testclient"）
    不当作反代。
    """
    peer = request.client.host if request.client else ""
    if peer and _is_internal(peer):
        real = (request.headers.get("X-Real-IP") or "").strip()
        if real:
            return real
    return peer or "unknown"


def _is_internal(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private


class SlidingWindowLimiter:
    """滑动窗口计数：窗口内放行 ``limit`` 次，超出返回需等待的秒数。"""

    def __init__(self, limit: int, window_seconds: float, *, max_keys: int = 4096):
        self._limit = max(int(limit), 1)
        self._window = float(window_seconds)
        self._max_keys = max(int(max_keys), 1)
        self._hits: dict[str, list[float]] = {}

    def __len__(self) -> int:
        return len(self._hits)

    def hit(self, key: str, *, now: float | None = None) -> tuple[bool, float]:
        """记一次尝试并判定；返回 (是否放行, 需等待秒数)。"""
        current = time.monotonic() if now is None else now
        hits = self._hits.get(key)
        if hits is None:
            hits = self._hits[key] = []
        else:
            cutoff = current - self._window
            while hits and hits[0] <= cutoff:
                hits.pop(0)
        if len(hits) >= self._limit:
            return False, max(hits[0] + self._window - current, 0.0)
        hits.append(current)
        self._evict_over_capacity()
        return True, 0.0

    def _evict_over_capacity(self) -> None:
        if len(self._hits) <= self._max_keys:
            return
        for key in [k for k, v in self._hits.items() if not v]:
            del self._hits[key]
        while len(self._hits) > self._max_keys:
            oldest = min(self._hits, key=lambda k: self._hits[k][0])
            del self._hits[oldest]


class DailyQuota:
    """按自然日（服务器本地时区）计数的配额；跨日自动归零。"""

    def __init__(self, limit: int, *, max_keys: int = 100_000):
        self._limit = max(int(limit), 0)
        self._max_keys = max(int(max_keys), 1)
        self._counts: dict[str, tuple[str, int]] = {}

    def check(self, key: str, *, today: str | None = None) -> tuple[bool, int]:
        """返回 (是否还有配额, 剩余次数)；不记账。"""
        day = today or time.strftime("%Y-%m-%d")
        rec = self._counts.get(key)
        used = rec[1] if rec is not None and rec[0] == day else 0
        return used < self._limit, max(self._limit - used, 0)

    def consume(self, key: str, *, today: str | None = None) -> None:
        day = today or time.strftime("%Y-%m-%d")
        rec = self._counts.get(key)
        used = rec[1] if rec is not None and rec[0] == day else 0
        self._counts[key] = (day, used + 1)
        self._evict_over_capacity(day)

    def _evict_over_capacity(self, day: str) -> None:
        if len(self._counts) <= self._max_keys:
            return
        for key in [k for k, (d, _n) in self._counts.items() if d != day]:
            del self._counts[key]
        while len(self._counts) > self._max_keys:
            self._counts.pop(next(iter(self._counts)))


def seconds_until_midnight(*, now: float | None = None) -> float:
    """距本地午夜的秒数（日配额的 Retry-After）。"""
    current = time.time() if now is None else now
    lt = time.localtime(current)
    tomorrow = time.mktime(
        (lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 0, 0, 0, 0, -1))
    return max(tomorrow - current, 1.0)


class RateLimits:
    """按配置装配的限流器组；由 ``init_deps`` 挂到 ``app.state``。"""

    def __init__(self, cfg):
        self.login_by_ip = SlidingWindowLimiter(
            _knob(cfg, "login_rate_limit", DEFAULT_LOGIN_LIMIT), 60)
        self.login_by_account = SlidingWindowLimiter(
            _knob(cfg, "login_account_rate_limit", DEFAULT_LOGIN_ACCOUNT_LIMIT), 60)
        self.register_by_ip = SlidingWindowLimiter(
            _knob(cfg, "register_rate_limit", DEFAULT_REGISTER_LIMIT), 3600)
        self.turn_quota = DailyQuota(
            _knob(cfg, "turn_daily_quota", DEFAULT_TURN_DAILY_QUOTA))


def _knob(cfg, name: str, default: int) -> int:
    return int(getattr(cfg, name, default) or 0)


def retry_after_seconds(seconds: float) -> str:
    """Retry-After 头取值：整数秒，至少 1。"""
    return str(max(1, math.ceil(seconds)))
