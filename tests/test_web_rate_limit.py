# tests/test_web_rate_limit.py
"""S02：全链路限流——登录/注册按 IP（登录另按账号），建回合按 owner 日配额。

限流器是**进程内**状态：当前部署是单进程 uvicorn（systemd ExecStart），成立；
多 worker / 多实例部署需换共享后端（Redis 等），否则各进程各算一份配额。
"""
import pytest
from conftest import OWNER, make_client
from fieldwork_agent.web_host.app import create_app


@pytest.fixture
def app(fake_config):
    from fieldwork_agent.web_host.deps import init_deps

    _app = create_app(config=fake_config)
    init_deps(_app, config=fake_config)
    return _app


def _request(client, headers: dict | None = None):
    from starlette.requests import Request

    return Request({
        "type": "http", "method": "GET", "path": "/", "raw_path": b"/",
        "query_string": b"", "scheme": "http", "http_version": "1.1",
        "server": ("testserver", 80), "client": client,
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    })


# ---------------------------------------------------------------- 客户端 IP
def test_client_ip_trusts_real_ip_from_loopback():
    """nginx 反代后 peer 恒为 127.0.0.1，真实 IP 只能从 X-Real-IP 取。"""
    from fieldwork_agent.web_host.ratelimit import client_ip

    req = _request(("127.0.0.1", 5000), {"X-Real-IP": "203.0.113.7"})
    assert client_ip(req) == "203.0.113.7"


def test_client_ip_ignores_real_ip_from_public_peer():
    """直连公网端口时客户端可自带 X-Real-IP——不信任，按真实 peer 记账。"""
    from fieldwork_agent.web_host.ratelimit import client_ip

    req = _request(("8.8.8.8", 5000), {"X-Real-IP": "198.51.100.1"})
    assert client_ip(req) == "8.8.8.8"


def test_client_ip_without_client_falls_back_to_unknown():
    from fieldwork_agent.web_host.ratelimit import client_ip

    assert client_ip(_request(None)) == "unknown"


# ---------------------------------------------------------------- 滑动窗口
def test_window_allows_limit_then_rejects_with_retry_after():
    from fieldwork_agent.web_host.ratelimit import SlidingWindowLimiter

    lim = SlidingWindowLimiter(limit=3, window_seconds=60)
    assert [lim.hit("ip", now=t)[0] for t in (0, 1, 2)] == [True, True, True]
    allowed, retry = lim.hit("ip", now=3)
    assert allowed is False
    assert 57 <= retry <= 60


def test_window_slides_after_expiry():
    from fieldwork_agent.web_host.ratelimit import SlidingWindowLimiter

    lim = SlidingWindowLimiter(limit=2, window_seconds=60)
    assert lim.hit("ip", now=0)[0] is True
    assert lim.hit("ip", now=1)[0] is True
    assert lim.hit("ip", now=2)[0] is False
    assert lim.hit("ip", now=60.5)[0] is True


def test_rejections_do_not_extend_the_window():
    """拒绝不记账：否则持续撞墙会让该键永远出不来（封锁自我延长）。"""
    from fieldwork_agent.web_host.ratelimit import SlidingWindowLimiter

    lim = SlidingWindowLimiter(limit=1, window_seconds=60)
    assert lim.hit("ip", now=0)[0] is True
    for t in range(1, 59):
        assert lim.hit("ip", now=t)[0] is False
    assert lim.hit("ip", now=61)[0] is True


def test_keys_are_independent():
    from fieldwork_agent.web_host.ratelimit import SlidingWindowLimiter

    lim = SlidingWindowLimiter(limit=1, window_seconds=60)
    assert lim.hit("a", now=0)[0] is True
    assert lim.hit("b", now=0)[0] is True
    assert lim.hit("a", now=1)[0] is False


def test_key_storage_is_bounded():
    """键来自 IP/邮箱等外部输入，必须有界——否则限流器自己成了内存放大器。"""
    from fieldwork_agent.web_host.ratelimit import SlidingWindowLimiter

    lim = SlidingWindowLimiter(limit=1, window_seconds=60, max_keys=4)
    for i in range(50):
        lim.hit(f"k{i}", now=i)
    assert len(lim) <= 4


# ---------------------------------------------------------------- 日配额
def test_daily_quota_allows_until_limit():
    from fieldwork_agent.web_host.ratelimit import DailyQuota

    q = DailyQuota(limit=2)
    assert q.check("u", today="2026-10-20") == (True, 2)
    q.consume("u", today="2026-10-20")
    assert q.check("u", today="2026-10-20") == (True, 1)
    q.consume("u", today="2026-10-20")
    assert q.check("u", today="2026-10-20") == (False, 0)


def test_daily_quota_resets_on_new_day():
    from fieldwork_agent.web_host.ratelimit import DailyQuota

    q = DailyQuota(limit=1)
    q.consume("u", today="2026-10-20")
    assert q.check("u", today="2026-10-20") == (False, 0)
    assert q.check("u", today="2026-10-21") == (True, 1)


# ---------------------------------------------------------------- 登录 / 注册
async def test_login_11th_attempt_in_a_minute_gets_429(app, monkeypatch):
    """验收：单 IP 60 秒内第 11 次登录尝试返回 429 + Retry-After。"""
    # 本用例只验计数；600k PBKDF2 换成空实现，避免 11 次哈希拖慢测试
    monkeypatch.setattr(app.state.user_store, "burn_password_check", lambda pw: None)
    async with make_client(app, owner_token=None) as c:
        for _ in range(10):
            r = await c.post("/auth/login",
                             json={"email": "x@b.com", "password": "wrong-pass-1"})
            assert r.status_code == 401
        r = await c.post("/auth/login",
                         json={"email": "x@b.com", "password": "wrong-pass-1"})
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "rate_limited"
    assert int(r.headers["Retry-After"]) >= 1


async def test_register_rate_limited_per_ip(app):
    from fieldwork_agent.web_host.ratelimit import RateLimits

    app.state.config.register_rate_limit = 2
    app.state.rate_limits = RateLimits(app.state.config)
    async with make_client(app, owner_token=None) as c:
        for i in range(2):
            r = await c.post("/auth/register",
                             json={"email": f"r{i}@b.com", "password": "s3cret-pass"})
            assert r.status_code == 201
        r = await c.post("/auth/register",
                         json={"email": "r3@b.com", "password": "s3cret-pass"})
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "rate_limited"


# ---------------------------------------------------------------- 回合日配额
async def test_turn_quota_exhausted_blocks_before_model_call(app, monkeypatch):
    """验收：配额耗尽后建回合 429，且不排后台任务（不产生模型调用）。"""
    import fieldwork_agent.web_host.api.turns as turns_mod
    from fieldwork_agent.web_host.ratelimit import RateLimits

    app.state.config.turn_daily_quota = 1
    app.state.rate_limits = RateLimits(app.state.config)
    app.state.llm = object()          # 有 key：让 403 llm_key_required 不抢先
    calls: list[tuple] = []
    monkeypatch.setattr(turns_mod, "_execute_turn", lambda *a, **k: calls.append(a))

    async with make_client(app) as c:
        assert (await c.post("/projects",
                             json={"project_id": "p1"})).status_code in (200, 201)
        r1 = await c.post("/projects/p1/messages", json={"text": "hi"})
        assert r1.status_code == 201
        r2 = await c.post("/projects/p1/messages", json={"text": "again"})
        assert r2.status_code == 429
        assert r2.json()["detail"]["code"] == "turn_quota_exceeded"
        assert int(r2.headers["Retry-After"]) >= 1
    assert len(calls) == 1            # 只有第一次回合进入了执行


async def test_idempotent_hit_does_not_consume_quota(app, monkeypatch):
    import fieldwork_agent.web_host.api.turns as turns_mod
    from fieldwork_agent.web_host.ratelimit import RateLimits

    app.state.config.turn_daily_quota = 1
    app.state.rate_limits = RateLimits(app.state.config)
    app.state.llm = object()
    monkeypatch.setattr(turns_mod, "_execute_turn", lambda *a, **k: None)

    async with make_client(app) as c:
        assert (await c.post("/projects",
                             json={"project_id": "p1"})).status_code in (200, 201)
        r1 = await c.post("/projects/p1/messages",
                          json={"text": "hi", "idempotency_key": "k1"})
        assert r1.status_code == 201
        again = await c.post("/projects/p1/messages",
                             json={"text": "hi", "idempotency_key": "k1"})
        assert again.status_code == 201
        assert again.json()["turn_id"] == r1.json()["turn_id"]
        # 新回合：配额已被第一次消耗 → 429（幂等命中不该额外扣一次）
        r2 = await c.post("/projects/p1/messages",
                          json={"text": "next", "idempotency_key": "k2"})
        assert r2.status_code == 429


async def test_retry_respects_turn_quota(app):
    from fieldwork_agent.web_host.ratelimit import RateLimits

    app.state.config.turn_daily_quota = 0
    app.state.rate_limits = RateLimits(app.state.config)
    app.state.llm = object()
    repo, tm = app.state.repo, app.state.turn_manager
    repo.create("p1", owner=OWNER)
    turn = tm.create_turn("p1")
    tm.finish("p1", turn.turn_id, "completed")
    repo.append_message("p1", "user", "问题一", {"turn_id": turn.turn_id})
    repo.append_message("p1", "assistant", "回答一", {"turn_id": turn.turn_id})

    async with make_client(app) as c:
        rr = await c.post(f"/projects/p1/turns/{turn.turn_id}/retry")
        assert rr.status_code == 429
        assert rr.json()["detail"]["code"] == "turn_quota_exceeded"
        # 配额拒绝先于弹消息：历史必须原样保留
        assert len(repo.get_messages("p1", limit=0)) == 2
