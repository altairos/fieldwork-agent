"""kernel/sources/flyai — 8 原子搜索原语与结果归一化（全部基于 mock，不联网不起子进程）。"""
import pytest
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


def test_search_flights_argv_full_order(monkeypatch):
    """可选参数按旧 tools/flyai_tools.py 的顺序拼装，空值不出现。"""
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
