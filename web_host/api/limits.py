"""S02：限流的 HTTP 外壳——判定在 ``web_host.ratelimit``，这里只折成 429。

429 统一带 ``Retry-After``（整数秒）：前端与 nginx 都按同一语义退避。
"""

from fastapi import HTTPException, Request
from fieldwork_agent.web_host.deps import get_rate_limits
from fieldwork_agent.web_host.ratelimit import (
    client_ip,
    retry_after_seconds,
    seconds_until_midnight,
)


def _too_many(seconds: float, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={"code": code, "message": message},
        headers={"Retry-After": retry_after_seconds(seconds)},
    )


def enforce_window(limiter, key: str, *, code: str, message: str) -> None:
    """窗口限流：超限抛 429（带 Retry-After）。"""
    allowed, retry_after = limiter.hit(key)
    if not allowed:
        raise _too_many(retry_after, code, message)


def require_turn_quota(request: Request, owner: str | None) -> str:
    """检查 owner 当日回合配额；返回配额键供成功创建后记账。

    无令牌客户端按 IP 记账——否则所有匿名请求共享同一个键，先到者
    能把匿名配额一次用光。
    """
    key = owner or f"ip:{client_ip(request)}"
    allowed, _remaining = get_rate_limits(request).turn_quota.check(key)
    if not allowed:
        raise _too_many(
            seconds_until_midnight(), "turn_quota_exceeded",
            "今日回合配额已用完，请明天再试；自建服务可调整 FA_TURN_DAILY_QUOTA")
    return key


def consume_turn_quota(request: Request, key: str) -> None:
    """回合已成功创建、即将进入模型调用时才记账（失败创建不扣配额）。"""
    get_rate_limits(request).turn_quota.consume(key)
