"""flyai_tools — 8 个工具的命令行拼装 & 子进程异常处理。"""
import json
import subprocess

import pytest
from fieldwork_agent.tools.flyai_tools import FlyAITools


@pytest.fixture
def recorder(monkeypatch):
    """拦截 _run_command，记录传入的 argv 并返回可预测结果。"""
    calls = []

    def _fake(args):
        calls.append(list(args))
        return {"status": 0, "data": {"echo": list(args)}}

    tools = FlyAITools(api_key="test-key")
    monkeypatch.setattr(tools, "_run_command", _fake)
    return tools, calls


# ─── argv 拼装（纯字符串逻辑，无需 mock 子进程）───────────────

def test_search_flight_full(recorder):
    tools, calls = recorder
    out = tools.search_flight("北京", "大理", dep_date="2026-07-01",
                              back_date="2026-09-01", sort_type="3", max_price="2000")
    assert out["status"] == 0
    assert calls == [[
        "search-flight", "--origin", "北京", "--destination", "大理",
        "--dep-date", "2026-07-01", "--back-date", "2026-09-01",
        "--sort-type", "3", "--max-price", "2000",
    ]]


def test_search_flight_minimal_defaults(recorder):
    tools, calls = recorder
    tools.search_flight("上海", "昆明")
    # 默认 sort_type="2"，无日期/价格
    assert calls[0] == [
        "search-flight", "--origin", "上海", "--destination", "昆明",
        "--sort-type", "2",
    ]


def test_search_train(recorder):
    tools, calls = recorder
    tools.search_train("成都", "拉萨", dep_date="2026-07-01",
                       seat_class="二等座", journey_type="单程")
    assert calls[0] == [
        "search-train", "--origin", "成都", "--destination", "拉萨",
        "--dep-date", "2026-07-01", "--seat-class-name", "二等座",
        "--journey-type", "单程", "--sort-type", "4",
    ]


def test_search_hotel(recorder):
    tools, calls = recorder
    tools.search_hotel("大理", check_in="2026-07-01", check_out="2026-07-10",
                       hotel_types="民宿,客栈", max_price="300",
                       sort="distance_asc", poi_name="洱海", hotel_stars="3")
    assert calls[0] == [
        "search-hotel", "--dest-name", "大理",
        "--check-in-date", "2026-07-01", "--check-out-date", "2026-07-10",
        "--hotel-types", "民宿,客栈", "--max-price", "300",
        "--sort", "distance_asc", "--poi-name", "洱海", "--hotel-stars", "3",
    ]


def test_search_poi(recorder):
    tools, calls = recorder
    tools.search_poi("大理", keyword="庙", category="宗教场所", poi_level="4")
    assert calls[0] == [
        "search-poi", "--city-name", "大理",
        "--keyword", "庙", "--category", "宗教场所", "--poi-level", "4",
    ]


def test_keyword_search(recorder):
    tools, calls = recorder
    tools.keyword_search("签证")
    assert calls[0] == ["keyword-search", "--query", "签证"]


def test_ai_search(recorder):
    tools, calls = recorder
    tools.ai_search("适合长期驻村的便宜住宿")
    assert calls[0] == ["ai-search", "--query", "适合长期驻村的便宜住宿"]


def test_search_marriott_hotel(recorder):
    tools, calls = recorder
    tools.search_marriott_hotel("杭州", check_in="2026-07-01",
                                hotel_brands="万丽", max_price="1200")
    assert calls[0] == [
        "search-marriott-hotel", "--dest-name", "杭州",
        "--check-in-date", "2026-07-01", "--hotel-brands", "万丽",
        "--max-price", "1200", "--sort", "price_asc",
    ]


def test_search_marriott_package(recorder):
    tools, calls = recorder
    tools.search_marriott_package("下午茶", sort_type="price_asc")
    assert calls[0] == [
        "search-marriott-package", "--keyword", "下午茶", "--sort-type", "price_asc",
    ]


def test_all_eight_tools_have_distinct_commands(recorder):
    tools, calls = recorder
    tools.search_flight("A", "B")
    tools.search_train("A", "B")
    tools.search_hotel("B")
    tools.search_poi("B")
    tools.keyword_search("q")
    tools.ai_search("q")
    tools.search_marriott_hotel("B")
    tools.search_marriott_package("q")
    cmds = [c[0] for c in calls]
    assert cmds == [
        "search-flight", "search-train", "search-hotel", "search-poi",
        "keyword-search", "ai-search", "search-marriott-hotel",
        "search-marriott-package",
    ]


# ─── _run_command：真实子进程封装的异常分支 ───────────────

def test_run_command_success(monkeypatch):
    tools = FlyAITools()
    payload = {"status": 0, "data": {"ok": True}}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        args=a[0], returncode=0, stdout=json.dumps(payload), stderr=""))
    assert tools._run_command(["ai-search", "--query", "x"]) == payload


def test_run_command_nonzero_returncode(monkeypatch):
    tools = FlyAITools()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        args=a[0], returncode=1, stdout="", stderr="boom"))
    res = tools._run_command(["search-hotel"])
    assert res["status"] == -1
    assert res["error"] == "boom"


def test_run_command_cli_not_found(monkeypatch):
    tools = FlyAITools()

    def _raise(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", _raise)
    res = tools._run_command(["flyai"])
    assert res == {"status": -1, "error": "flyai CLI not found"}


def test_run_command_timeout(monkeypatch):
    tools = FlyAITools()

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="flyai", timeout=30)

    monkeypatch.setattr(subprocess, "run", _raise)
    res = tools._run_command(["flyai"])
    assert res["status"] == -1
    assert "timed out" in res["error"]


def test_run_command_bad_json(monkeypatch):
    tools = FlyAITools()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        args=a[0], returncode=0, stdout="not-json", stderr=""))
    res = tools._run_command(["flyai"])
    assert res["status"] == -1
    assert res["error"].startswith("JSON parse error")
