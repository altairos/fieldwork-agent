"""kernel/sources/flyai — 8 原子搜索原语与结果归一化（全部基于 mock，不联网不起子进程）。"""
import datetime as dt
import json
from pathlib import Path

import fieldwork_agent.kernel.sources.flyai as flyai_module
import pytest
from fieldwork_agent.kernel.models import Schedule
from fieldwork_agent.kernel.sources.flyai import FlyAIClient, normalize_items, parse_price

# ---------------------------------------------------------------- 价格归一化


@pytest.mark.parametrize("raw,expected", [
    ("¥1,234", 1234.0), ("¥1,234.50", 1234.5), ("1234", 1234.0),
    ("¥999起", 999.0), ("", None), (None, None), ("免费", None)])
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected

# ---------------------------------------------------------------- 条目归一化


def test_normalize_items_maps_fields():
    raw = [{"name": "A民宿", "adultPrice": "¥200", "mainPic": "p.jpg",
            "jumpUrl": "j", "unknownField": 1}]
    items = normalize_items(raw)
    assert items[0].name == "A民宿" and items[0].price == 200.0
    assert items[0].pic_url == "p.jpg" and items[0].extra == {"unknownField": 1}


def test_normalize_items_falls_back_to_alt_keys():
    """title/picUrl/detailUrl 等别名键同样被识别，且不残留在 extra。"""
    items = normalize_items([{"title": "B", "price": "¥80",
                              "picUrl": "x.jpg", "detailUrl": "d"}])
    assert items[0].name == "B" and items[0].price == 80.0
    assert items[0].pic_url == "x.jpg" and items[0].jump_url == "d"
    assert items[0].extra == {}


def test_normalize_items_unwraps_info_nesting():
    items = normalize_items([{"info": {"title": "彝文化实景剧", "price": None}}])
    assert items[0].name == "彝文化实景剧" and items[0].price is None

# ---------------------------------------------------------------- argv 拼装


def test_search_hotel_argv_and_envelope(monkeypatch):
    calls = {}
    def fake_run(args):
        calls["args"] = args
        return {"status": 0, "data": {"itemList": [{"title": "B", "price": "¥80"}]}}
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command", fake_run)
    env = client.search_hotels("大理", hotel_types="民宿,客栈", max_price="200")
    assert env.ok and env.data.items[0].name == "B" and env.data.items[0].price == 80.0
    assert env.data.cheapest == 80.0
    assert calls["args"] == ["search-hotel", "--dest-name", "大理",
                             "--hotel-types", "民宿,客栈", "--max-price", "200",
                             "--sort", "price_asc"]


# ---------------------------------------------------------------- S07 argv 注入守卫


def _guarded_client(monkeypatch):
    calls = []
    client = FlyAIClient()
    monkeypatch.setattr(
        client, "_run_command",
        lambda args: calls.append(args) or {"status": 0, "data": {"itemList": []}})
    return client, calls


@pytest.mark.parametrize("evil", ["--sort-type", "-x", "--origin"])
def test_argv_value_starting_with_dash_rejected(monkeypatch, evil):
    """S07：值以 - 开头会被下游 CLI 当选项解析（参数走私），出网前拒绝。"""
    client, calls = _guarded_client(monkeypatch)
    future = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    env = client.search_flights(evil, "北京", future)
    assert not env.ok and env.error.code == "invalid_arguments"
    assert calls == []


def test_argv_control_chars_rejected(monkeypatch):
    client, calls = _guarded_client(monkeypatch)
    env = client.search_keyword("a\x00b")
    assert not env.ok and env.error.code == "invalid_arguments"
    assert calls == []


def test_argv_guard_allows_normal_values(monkeypatch):
    client, calls = _guarded_client(monkeypatch)
    future = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    env = client.search_flights("昆明", "北京", future)
    assert env.ok and calls and calls[0][0] == "search-flight"


def test_search_flights_argv_full_order(monkeypatch):
    """可选参数按旧 tools/flyai_tools.py 的顺序拼装，空值不出现。"""
    monkeypatch.setattr(flyai_module, "_today", lambda: dt.date(2026, 10, 18))
    calls = {}
    def fake_run(args):
        calls["args"] = args
        return {"status": 0, "data": {"itemList": []}}
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command", fake_run)
    env = client.search_flights("北京", "大理", dep_date="2026-11-01", max_price="1500")
    assert env.ok and env.data.total == 0
    assert calls["args"] == ["search-flight", "--origin", "北京", "--destination", "大理",
                             "--dep-date", "2026-11-01", "--sort-type", "2",
                             "--max-price", "1500"]


def test_search_pois_argv(monkeypatch):
    calls = {}
    def fake_run(args):
        calls["args"] = args
        return {"status": 0, "data": {"itemList": []}}
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command", fake_run)
    client.search_pois("大理", category="宗教场所", poi_level="4")
    assert calls["args"] == ["search-poi", "--city-name", "大理",
                             "--category", "宗教场所", "--poi-level", "4"]

# ---------------------------------------------------------------- 错误降级


def test_cli_missing_degrades(monkeypatch):
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command",
                        lambda a: {"status": -1, "error": "flyai CLI not found"})
    env = client.search_ai("q")
    assert not env.ok and env.error.code == "flyai_unavailable"


def test_timeout_and_generic_and_parse_errors(monkeypatch):
    cases = [
        ("Command timed out after 30s", "flyai_timeout"),
        ("boom", "flyai_error"),
        ("JSON parse error: Expecting value", "parse_error"),
    ]
    for msg, code in cases:
        client = FlyAIClient()
        monkeypatch.setattr(client, "_run_command", lambda a, m=msg: {"status": -1, "error": m})
        env = client.search_keyword("q")
        assert not env.ok and env.error.code == code
        assert env.error.message  # 约定：error 必带 code 与 message


def test_error_envelope_carries_hint(monkeypatch):
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command",
                        lambda a: {"status": -1, "error": "flyai CLI not found"})
    env = client.search_marriott_packages("三亚")
    assert env.error.hint  # 降级提示不可为空，便于宿主给出下一步建议


def test_rate_limit_markers_map_to_quota_code_and_hint(monkeypatch):
    """每日共享配额用尽（宽匹配常见限流文案）→ flyai_rate_limited + 配额说明 hint。

    未抓过真实触发样本，宁可多匹配：rate limit / too many requests / quota /
    429 / 调用次数 / 限流 等常见模式都归入配额分支，hint 引导申请自己的 API Key。
    """
    cases = [
        "rate limited",
        "Too Many Requests",
        "API quota exceeded",
        "HTTP 429: forbidden",
        "超出每日调用次数上限",
        "请求触发限流，请稍后再试",
    ]
    for msg in cases:
        client = FlyAIClient()
        monkeypatch.setattr(client, "_run_command",
                            lambda a, m=msg: {"status": -1, "error": m})
        env = client.search_keyword("q")
        assert not env.ok and env.error.code == "flyai_rate_limited", msg
        assert "飞猪 API 配额说明" in env.error.hint
        assert "https://flyai.open.fliggy.com/" in env.error.hint


# ---------------------------------------------------------------- 子进程 env 注入（审计 F05）


def test_run_command_injects_page_level_key(monkeypatch):
    """审计 F05：回合级实例（带页面 key）把 FLYAI_API_KEY 注入子进程 env。"""
    import os as _os

    from fieldwork_agent.kernel.sources import flyai as flyai_mod

    captured: dict = {}

    class _Result:
        returncode = 0
        stdout = '{"status": 0, "data": {"itemList": []}}'
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr(flyai_mod.subprocess, "run", fake_run)
    client = flyai_mod.FlyAIClient(api_key="page-key")
    out = client._run_command(["searchHotels", "--dest", "大理"])
    assert out["status"] == 0
    assert captured["env"]["FLYAI_API_KEY"] == "page-key"
    assert captured["env"]["PATH"] == _os.environ["PATH"]  # 继承而非替换整个环境


def test_run_command_no_key_passes_env_none(monkeypatch):
    """服务端单例（无 key）不动子进程 env——CLI 回落自身配置文件。"""
    from fieldwork_agent.kernel.sources import flyai as flyai_mod

    captured: dict = {}

    class _Result:
        returncode = 0
        stdout = '{"status": 0, "data": {"itemList": []}}'
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr(flyai_mod.subprocess, "run", fake_run)
    client = flyai_mod.FlyAIClient(api_key="")
    client._run_command(["ping"])
    assert captured["env"] is None

# ---------------------------------------------------------------- Windows 兼容（本机联调修复）


class _OkResult:
    returncode = 0
    stdout = '{"status": 0, "data": {"itemList": [{"title": "邛海民宿"}]}}'
    stderr = ""


def test_run_command_resolves_executable_via_which(monkeypatch):
    """Windows：shutil.which 按 PATHEXT 解析 flyai.cmd 全路径作 argv[0]。

    CreateProcess 不解析 PATHEXT（只认 .exe），裸名 "flyai" 直接
    FileNotFoundError——npm 全局装的 .cmd/.ps1 入口必须先解析成全路径。
    """
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _OkResult()

    monkeypatch.setattr(flyai_module.shutil, "which", lambda name: r"C:\npm\flyai.cmd")
    monkeypatch.setattr(flyai_module.subprocess, "run", fake_run)
    out = FlyAIClient()._run_command(["ping"])
    assert out["status"] == 0
    assert captured["cmd"][0] == r"C:\npm\flyai.cmd"


def test_run_command_which_miss_falls_back_to_bare_name(monkeypatch):
    """which 未命中（未安装/不在 PATH）回落裸名——保留原 FileNotFoundError 降级链。"""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _OkResult()

    monkeypatch.setattr(flyai_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(flyai_module.subprocess, "run", fake_run)
    out = FlyAIClient()._run_command(["ping"])
    assert out["status"] == 0
    assert captured["cmd"][0] == "flyai"


def test_run_command_recovers_complete_stdout_on_crash_exit(monkeypatch):
    """flyai CLI 1.0.16 Windows 退出阶段 libuv 断言崩溃（exit 0xC0000409）：
    stdout JSON 已完整写出——按内容判定而非退出码，否则成功调用被误判失败。"""

    class _Crashed:
        returncode = 3221226505  # 0xC0000409
        stdout = '{"status": 0, "data": {"itemList": [{"title": "邛海民宿"}]}}'
        stderr = ("Assertion failed: !(handle->flags & UV_HANDLE_CLOSING), "
                  "file src\\win\\async.c, line 76")

    monkeypatch.setattr(flyai_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(flyai_module.subprocess, "run", lambda cmd, **kw: _Crashed())
    out = FlyAIClient()._run_command(["ping"])
    assert out["status"] == 0
    assert out["data"]["itemList"][0]["title"] == "邛海民宿"


def test_run_command_nonzero_exit_invalid_stdout_still_errors(monkeypatch):
    """真失败（stdout 空/非 JSON）不宽容——仍走 stderr 降级。"""

    class _Failed:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(flyai_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(flyai_module.subprocess, "run", lambda cmd, **kw: _Failed())
    out = FlyAIClient()._run_command(["ping"])
    assert out == {"status": -1, "error": "boom"}


def test_run_command_nonzero_exit_error_payload_degrades(monkeypatch):
    """stdout 是 JSON 但 status!=0（CLI 自报失败）：不因宽容被吞，原样透传。"""

    class _SelfReported:
        returncode = 2
        stdout = '{"status": -1, "error": "rate limited"}'
        stderr = ""

    monkeypatch.setattr(flyai_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(flyai_module.subprocess, "run", lambda cmd, **kw: _SelfReported())
    out = FlyAIClient()._run_command(["ping"])
    assert out == {"status": -1, "error": "rate limited"}


def test_run_command_decodes_utf8(monkeypatch):
    """显式 UTF-8 解码：text=True 默认随 locale（Windows cp936），中文结果必乱码。"""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _OkResult()

    monkeypatch.setattr(flyai_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(flyai_module.subprocess, "run", fake_run)
    FlyAIClient()._run_command(["ping"])
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"

# ── journeys 深层解析（spec §3.2）──────────────────────────────
_FIXTURE = Path(__file__).parent / "fixtures" / "flyai_responses" / "flight_journeys.json"


def test_journeys_shape_normalized():
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    env = flyai_module._page_envelope(payload)
    assert env.ok and env.data.total == 2
    first, second = env.data.items
    # name = 承运 班次 起飞-到达；直飞一段
    assert first.name == "东方航空 MU5703 08:30-10:05"
    assert first.price == 620.0
    assert first.schedule == Schedule(dep_time="08:30", arr_time="10:05",
                                      duration_minutes=95, stops=0, cabin="经济舱",
                                      departure_place="昆明长水", arrival_place="西昌青山",
                                      segments=payload["data"]["journeys"][0]["segments"], stops_kind="connections")
    # 中转：segments 拼接，stops=1，取末段到达
    assert second.name == "祥鹏航空 8L9751 14:10-18:10"
    assert second.schedule is not None and second.schedule.stops == 1


def test_itemlist_path_schedule_none():
    """平铺路径行为不变：schedule 为 None（酒店/POI 消费者不受影响）。"""
    env = flyai_module._page_envelope(
        {"status": 0, "data": {"itemList": [{"name": "古城民宿", "price": "¥150"}]}})
    assert env.ok and env.data.items[0].schedule is None
    assert env.data.items[0].name == "古城民宿"


# ── systemMessage 体验模式透传（spec §3.2 数据地基）─────────────


def test_system_message_passthrough():
    env = flyai_module._page_envelope({
        "status": 0, "systemMessage": "当前为体验模式，价格已脱敏",
        "data": {"itemList": [{"name": "民宿", "price": "¥150"}]}})
    assert env.ok and env.degraded == "当前为体验模式，价格已脱敏"
    assert env.data.items[0].price == 150.0


def test_system_message_inside_data():
    env = flyai_module._page_envelope({
        "status": 0, "data": {"systemMessage": "脱敏模式",
                              "itemList": [{"name": "民宿", "price": "¥150"}]}})
    assert env.ok and env.degraded == "脱敏模式"


def test_no_system_message_undegraded():
    env = flyai_module._page_envelope(
        {"status": 0, "data": {"itemList": [{"name": "民宿", "price": "¥150"}]}})
    assert env.degraded is None


# ------------------------------------------- 真实错误透传与日期错误码（轮 3 实测）

def test_execute_surfaces_message_key_error(monkeypatch):
    """status!=0 且无 error 键但有 message 键（飞猪对过去日期的真实形态
    CICO_CHECK_PARAM_ERROR）→ 原因进 error.message 并映射 flyai_invalid_date，
    不再吞成兜底文案"flyai 调用失败"让 LLM 无从自救。"""
    # 前置日期校验会先拦住过去日期；固定今天早于 check_in 以直达 CLI 错误路径
    monkeypatch.setattr(flyai_module, "_today", lambda: dt.date(2026, 10, 1))
    c = FlyAIClient()
    monkeypatch.setattr(c, "_run_command", lambda args: {
        "status": 1, "data": None, "systemMessage": None,
        "message": "[CICO_CHECK_PARAM_ERROR] AI hotel search fail: 入离时间校验失败",
    })
    env = c.search_hotels("晋江", check_in="2026-10-07", check_out="2026-10-17")
    assert env.ok is False and env.error is not None
    assert "入离时间校验失败" in env.error.message
    assert env.error.code == "flyai_invalid_date"


def test_error_envelope_invalid_date_hint():
    """入离时间校验失败 → flyai_invalid_date，hint 教 LLM 改用未来日期。"""
    env = flyai_module._error_envelope(
        "[CICO_CHECK_PARAM_ERROR] AI hotel search fail: 入离时间校验失败")
    assert env.error is not None
    assert env.error.code == "flyai_invalid_date"
    assert "未来日期" in env.error.hint

# ------------------------------------------- 前置日期校验（Task 7）


def test_past_dep_date_rejected_before_cli(monkeypatch):
    monkeypatch.setattr(flyai_module, "_today", lambda: dt.date(2026, 10, 18))
    called = []
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command", lambda a: called.append(a) or {"status": 0, "data": {}})
    env = client.search_trains("昆明", "大理", dep_date="2026-07-15")
    assert env.ok is False and env.error.code == "flyai_invalid_date"
    assert "2027-07-15" in env.error.hint
    assert "今天（2026-10-18）" in env.error.hint
    assert called == []                                  # 未启动 CLI


def test_today_and_future_dates_pass(monkeypatch):
    monkeypatch.setattr(flyai_module, "_today", lambda: dt.date(2026, 10, 18))
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command",
                        lambda a: {"status": 0, "data": {"itemList": []}})
    assert client.search_flights("北京", "大理", dep_date="2026-10-18").ok is True
    assert client.search_trains("昆明", "大理", dep_date="2026-12-01").ok is True


def test_back_date_and_hotel_dates_validated(monkeypatch):
    monkeypatch.setattr(flyai_module, "_today", lambda: dt.date(2026, 10, 18))
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command",
                        lambda a: {"status": 0, "data": {"itemList": []}})
    flights = client.search_flights("北京", "大理", dep_date="2026-11-01",
                                    back_date="2026-07-01")
    assert flights.error.code == "flyai_invalid_date" and "返程日期" in flights.error.message
    hotels = client.search_hotels("大理", check_in="2026-11-01", check_out="2026-10-01")
    assert hotels.error.code == "flyai_invalid_date" and "离店日期" in hotels.error.message
    marriott = client.search_marriott_hotels("大理", check_in="2026-07-01")
    assert marriott.error.code == "flyai_invalid_date"


def test_unparseable_date_passes_to_cli(monkeypatch):
    """非 YYYY-MM-DD（如「7月15日」）不做本地判定，交 CLI 报错并走 _error_envelope。"""
    monkeypatch.setattr(flyai_module, "_today", lambda: dt.date(2026, 10, 18))
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command",
                        lambda a: {"status": 0, "data": {"itemList": []}})
    assert client.search_trains("昆明", "大理", dep_date="7月15日").ok is True


def test_departure_date_illegal_maps_to_invalid_date(monkeypatch):
    client = FlyAIClient()
    monkeypatch.setattr(client, "_run_command",
                        lambda a: {"status": 1, "message": "出发日期非法"})
    env = client.search_trains("昆明", "大理")
    assert env.ok is False and env.error.code == "flyai_invalid_date"
    assert "今天" in env.error.hint or "未来" in env.error.hint
