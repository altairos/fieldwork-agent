"""kernel/sources/amap — 高德 REST 六原语与结果归一化（全部基于 mock _get，不联网、无 Key）。"""
import pytest
from fieldwork_agent.kernel.models import GeoLocation
from fieldwork_agent.kernel.registry import build_tool_schemas, capabilities
from fieldwork_agent.kernel.sources.amap import AMapClient

# ---------------------------------------------------------------- geocode


def test_geocode_maps_fields_and_params(monkeypatch):
    calls = {}

    def fake_get(path, params):
        calls["path"], calls["params"] = path, params
        return {"status": "1", "infocode": "10000", "count": "1", "geocodes": [{
            "province": "云南省", "city": "大理白族自治州", "district": "大理市",
            "township": [], "adcode": "532901",
            "location": "100.164245,25.602535", "formatted_address": "云南省大理白族自治州大理市"}]}

    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", fake_get)
    env = client.geocode("大理市喜洲镇", city="大理")
    assert env.ok and isinstance(env.data, GeoLocation)
    assert env.data.province == "云南省" and env.data.city == "大理白族自治州"
    assert env.data.district == "大理市" and env.data.township == ""
    assert env.data.adcode == "532901"
    assert env.data.location == "100.164245,25.602535"
    assert env.data.source == "amap"
    assert calls["path"] == "/v3/geocode/geo"
    assert calls["params"]["address"] == "大理市喜洲镇"
    assert calls["params"]["city"] == "大理"
    assert calls["params"]["key"] == "k"


def test_geocode_list_shaped_fields_flattened(monkeypatch):
    """v3 geocode 在高精度下 city/district/township 可能是 [str] 或 []：统一拍平。"""
    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", lambda p, q: {
        "infocode": "10000", "count": "1", "geocodes": [{
            "province": "云南省", "city": ["大理白族自治州"], "district": ["大理市"],
            "township": ["喜洲镇"], "adcode": "532901", "location": "1,2",
            "formatted_address": "云南省大理白族自治州大理市喜洲镇"}]})
    env = client.geocode("喜洲镇")
    assert env.data.city == "大理白族自治州"
    assert env.data.township == "喜洲镇"


def test_geocode_no_result_is_amap_no_result(monkeypatch):
    """海外/生僻地名解析失败 → amap_no_result：宿主的双源回退信号（转 FlyAI 搜索）。"""
    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get",
                        lambda p, q: {"infocode": "10000", "count": "0", "geocodes": []})
    env = client.geocode("Kiribati")
    assert env.ok is False and env.error.code == "amap_no_result"
    assert env.error.hint  # 回退引导不可缺 hint
    assert "正在开发中" in env.error.hint  # 境外功能缺口须明示，不静默跳过


# ---------------------------------------------------------------- regeocode


def test_regeocode_maps_fields_and_params(monkeypatch):
    calls = {}

    def fake_get(path, params):
        calls["path"], calls["params"] = path, params
        return {"infocode": "10000", "regeocode": {
            "formatted_address": "云南省大理白族自治州大理市喜洲镇",
            "addressComponent": {"province": "云南省", "city": "大理白族自治州",
                                 "district": "大理市", "township": "喜洲镇",
                                 "adcode": "532901"}}}

    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", fake_get)
    env = client.regeocode("100.164,25.602")
    assert env.ok and isinstance(env.data, GeoLocation)
    assert env.data.location == "100.164,25.602"      # 请求坐标回填（响应无此键）
    assert env.data.formatted_address.startswith("云南省")
    assert env.data.township == "喜洲镇" and env.data.adcode == "532901"
    assert calls["path"] == "/v3/geocode/regeo"
    assert calls["params"]["location"] == "100.164,25.602"


# ---------------------------------------------------------------- 搜索（text / around）


def test_text_search_normalizes_pois(monkeypatch):
    calls = {}

    def fake_get(path, params):
        calls["path"], calls["params"] = path, params
        return {"infocode": "10000", "count": "2", "pois": [
            {"name": "喜洲古镇", "type": "风景名胜;古镇", "address": "大理市喜洲镇",
             "location": "100.172,25.664", "tel": "0872-2456789", "id": "B0FFH0"}]}

    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", fake_get)
    env = client.text_search("卫生院", city="大理市", page_size=10)
    assert env.ok and env.data.total == 2
    item = env.data.items[0]
    assert item.name == "喜洲古镇" and item.price is None
    assert item.extra["address"] == "大理市喜洲镇"
    assert item.extra["location"] == "100.172,25.664"
    assert calls["path"] == "/v3/place/text"
    assert calls["params"]["keywords"] == "卫生院"
    assert calls["params"]["city"] == "大理市"
    assert calls["params"]["offset"] == 10


def test_text_search_empty_pois_is_ok_empty_page(monkeypatch):
    """搜索空结果不是错误（与 FlyAI 空页语义一致）：ok=True + total=0。"""
    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get",
                        lambda p, q: {"infocode": "10000", "count": "0", "pois": []})
    env = client.text_search("不存在的东西", city="大理市")
    assert env.ok and env.data.total == 0 and env.data.items == []


def test_around_search_sends_location_and_radius(monkeypatch):
    calls = {}

    def fake_get(path, params):
        calls["path"], calls["params"] = path, params
        return {"infocode": "10000", "count": "1", "pois": [
            {"name": "喜洲卫生院", "location": "100.171,25.665", "distance": "850",
             "address": "喜洲镇", "type": "医疗保健服务"}]}

    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", fake_get)
    env = client.around_search("诊所", location="100.172,25.664", radius=5000)
    assert env.ok and env.data.items[0].extra["distance"] == "850"
    assert calls["path"] == "/v3/place/around"
    assert calls["params"]["location"] == "100.172,25.664"
    assert calls["params"]["radius"] == 5000


# ---------------------------------------------------------------- 公交路径规划


def test_direction_transit_each_transit_becomes_item(monkeypatch):
    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", lambda p, q: {
        "infocode": "10000", "route": {"transits": [
            {"duration": "3600", "cost": "8.0", "walking_distance": "1200",
             "segments": [
                 {"walking": {"distance": "600"}, "bus": {"buslines": [{"name": "8路"}]}},
                 {"walking": {"distance": "600"}, "bus": None}]},
            {"duration": "5400", "cost": "6.0", "walking_distance": "300",
             "segments": [{"walking": {}, "bus": {"buslines": [
                 {"name": "班车"}, {"name": "支线2"}]}}]}]}})
    env = client.direction_transit("100.16,25.60", "100.17,25.66", city="大理")
    assert env.ok and env.data.total == 2
    first, second = env.data.items
    assert first.name == "8路"
    assert first.price == 8.0
    assert first.schedule.duration_minutes == 60 and first.schedule.stops == 0
    assert second.name == "班车 → 支线2"
    assert second.schedule.stops == 1 and second.price == 6.0
    assert env.data.cheapest == 6.0 and env.data.dearest == 8.0


def test_direction_transit_empty_transits_ok_empty_page(monkeypatch):
    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get",
                        lambda p, q: {"infocode": "10000", "route": {"transits": []}})
    env = client.direction_transit("100.16,25.60", "100.17,25.66")
    assert env.ok and env.data.total == 0


def test_direction_transit_cross_city_cityd_separate(monkeypatch):
    """跨城（M4）：city=出发市、cityd=到达市分传（v3 语义）；单 city 保持双填兼容。"""
    calls = {}

    def fake_get(path, params):
        calls["params"] = params
        return {"infocode": "10000", "route": {"transits": []}}

    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", fake_get)
    client.direction_transit("102.71,25.04", "100.20,25.05",
                             city="昆明市", cityd="大理白族自治州")
    assert calls["params"]["city"] == "昆明市"
    assert calls["params"]["cityd"] == "大理白族自治州"
    client.direction_transit("100.16,25.60", "100.17,25.66", city="大理")
    assert calls["params"]["cityd"] == "大理"  # 单 city 双填（同城）不破坏


def test_direction_transit_date_in_query_only_when_set(monkeypatch):
    """班次日期（N1）：date 非空进 query；空串不进（现有调用零影响）。"""
    calls = {}

    def fake_get(path, params):
        calls["params"] = params
        return {"infocode": "10000", "route": {"transits": []}}

    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", fake_get)
    client.direction_transit("100.16,25.60", "100.17,25.66", city="大理",
                             date="2026-10-19")
    assert calls["params"]["date"] == "2026-10-19"
    client.direction_transit("100.16,25.60", "100.17,25.66", city="大理")
    assert "date" not in calls["params"]  # 空串不进 query，老调用形状不变


# ---------------------------------------------------------------- 天气


def test_weather_forecast_casts(monkeypatch):
    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", lambda p, q: {
        "infocode": "10000", "forecasts": [{
            "city": "大理白族自治州", "adcode": "532900",
            "casts": [{"date": "2026-10-18", "dayweather": "晴", "nightweather": "多云",
                       "daytemp": "24", "nighttemp": "12"}]}]})
    env = client.weather("532900")
    assert env.ok and isinstance(env.data, list)
    assert env.data[0]["dayweather"] == "晴" and env.data[0]["date"] == "2026-10-18"


def test_weather_lives_extension(monkeypatch):
    """extensions=base（实况）走 lives 形状，同样归一为 list[dict]。"""
    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", lambda p, q: {
        "infocode": "10000", "lives": [
            {"province": "云南", "city": "大理市", "weather": "晴", "temperature": "21"}]})
    env = client.weather("大理市", extensions="base")
    assert env.ok and env.data[0]["weather"] == "晴"


def test_weather_no_forecast_is_amap_no_result(monkeypatch):
    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get",
                        lambda p, q: {"infocode": "10000", "forecasts": []})
    env = client.weather("000000")
    assert env.ok is False and env.error.code == "amap_no_result"


# ---------------------------------------------------------------- 无 Key 短路与错误映射


def test_missing_key_short_circuits_all_primitives(monkeypatch):
    """无 Key 不出网：六原语直接返回 amap_invalid_key 包络（M1 验收门槛）。"""
    client = AMapClient(api_key="")
    monkeypatch.setattr(client, "_get", lambda p, q: pytest.fail("无 Key 不得发起请求"))
    results = [
        client.geocode("大理"),
        client.regeocode("100.16,25.60"),
        client.text_search("卫生院", city="大理"),
        client.around_search("诊所", location="100.16,25.60"),
        client.direction_transit("100.16,25.60", "100.17,25.66"),
        client.weather("532900"),
    ]
    for env in results:
        assert env.ok is False and env.error.code == "amap_invalid_key"
        assert env.error.hint  # 引导去高德控制台申请


@pytest.mark.parametrize("infocode,code", [
    ("10001", "amap_invalid_key"),
    ("10003", "amap_quota"),
    ("10004", "amap_rate_limited"),
    ("10019", "amap_error"),
])
def test_infocode_error_mapping(monkeypatch, infocode, code):
    """官方 infocode 钉死映射：10001/10003/10004 特判，其余归 amap_error 兜底。"""
    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get",
                        lambda p, q: {"infocode": infocode, "info": "服务器应答"})
    env = client.geocode("大理")
    assert env.ok is False and env.error.code == code
    assert env.error.message  # code+message 必带


@pytest.mark.parametrize("local_info,code", [
    ("timeout: timed out", "amap_timeout"),
    ("JSON parse error: Expecting value", "parse_error"),
    ("urlopen error: connection refused", "amap_error"),
])
def test_transport_failures_map_to_codes(monkeypatch, local_info, code):
    """_get 本地失败以 {"infocode": "00000", "info": ...} 形状上报，按内容归类。"""
    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get",
                        lambda p, q: {"infocode": "00000", "info": local_info})
    env = client.text_search("q", city="大理")
    assert env.ok is False and env.error.code == code


# ---------------------------------------------------------------- 注册面（schema 单一来源）


def test_six_primitives_registered_with_amap_prefix():
    schemas = {t["function"]["name"]: t for t in build_tool_schemas()}
    for name in ("amap_geocode", "amap_regeocode", "amap_text_search",
                 "amap_around_search", "amap_direction_transit", "amap_weather"):
        assert name in schemas
        props = schemas[name]["function"]["parameters"]["properties"]
        assert "client" not in props  # 注入参数不进 schema
    meta = capabilities()["amap_weather"]
    assert meta.read_only is True and meta.open_world is True


def test_capability_fn_injects_client(monkeypatch):
    """capability fn 注入模式：meta.fn(**args, amap_client=stub) 透传——注入键与
    flyai 的 client 分道，宿主不会把 FlyAIClient 塞给 AMapClient（反之亦然）。"""
    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", lambda p, q: {
        "infocode": "10000", "count": "1", "geocodes": [
            {"province": "云南省", "city": "大理白族自治州", "district": "大理市",
             "adcode": "532901", "location": "1,2", "formatted_address": "云南省大理州大理市"}]})
    env = capabilities()["amap_geocode"].fn(address="大理", amap_client=client)
    assert env.ok and env.data.adcode == "532901"


# ---------------------------------------------------------------- N3 实例级 TTL 读缓存


def _geo_payload(addr: str, loc: str) -> dict:
    return {"infocode": "10000", "count": "1", "geocodes": [{
        "province": "云南省", "city": "昆明市", "district": "", "township": [],
        "adcode": "530102", "location": loc, "formatted_address": addr}]}


def test_cache_same_params_hit_once(monkeypatch):
    """同参二连发只打一次 _get（geocode 与 weather 双验证），命中计数 cache_hit。"""
    n = {"get": 0}

    def fake_get(path, params):
        n["get"] += 1
        if "weather" in path:
            return {"infocode": "10000", "forecasts": [
                {"casts": [{"date": "2026-10-18", "dayweather": "晴"}]}]}
        return _geo_payload(params["address"], "102.7,25.0")

    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", fake_get)
    e1 = client.geocode("昆明")
    e2 = client.geocode("昆明")          # 同参二连发（city 默认值归一后同键）
    assert n["get"] == 1
    assert e1.ok and e2.ok and e2.data.location == "102.7,25.0"
    assert client.stats["cache_hit"] == 1
    w1 = client.weather("昆明")
    w2 = client.weather("昆明")          # weather 也经同一缓存路径（六原语不偷懒）
    assert n["get"] == 2
    assert w1.ok and w2.ok
    assert client.stats["cache_hit"] == 2


def test_cache_expiry_refetches(monkeypatch):
    """TTL 过期（expire_at 已过）重发真实请求，cache_hit 不虚增。"""
    import time
    n = {"get": 0}

    def fake_get(path, params):
        n["get"] += 1
        return _geo_payload(params["address"], "102.7,25.0")

    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", fake_get)
    client.geocode("昆明")
    assert n["get"] == 1
    for key in client._cache:  # 白盒：把过期时间改到过去
        expire_at, env = client._cache[key]
        client._cache[key] = (time.monotonic() - 1, env)
    e2 = client.geocode("昆明")
    assert n["get"] == 2 and e2.ok      # 过期即重发，不足偶返回旧值
    assert client.stats["cache_hit"] == 0


def test_cache_param_diff_no_cross_talk(monkeypatch):
    """参数差异不串味：不同地名各自缓存，重打前一地名命中不再出网。"""
    n = {"get": 0}

    def fake_get(path, params):
        n["get"] += 1
        return _geo_payload(params["address"], f"loc-{params['address']}")

    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", fake_get)
    e1 = client.geocode("昆明")
    e2 = client.geocode("大理")
    assert n["get"] == 2
    assert e1.data.location == "loc-昆明" and e2.data.location == "loc-大理"
    e3 = client.geocode("昆明")          # 重打昆明 → 命中，不串味成大理的值
    assert n["get"] == 2 and e3.data.location == "loc-昆明"


def test_cache_deep_copy_prevents_pollution(monkeypatch):
    """深拷贝防污染：调用方改写返回包络不影响缓存，后续命中仍是原值。"""
    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get",
                        lambda p, q: _geo_payload(q["address"], "102.7,25.0"))
    e1 = client.geocode("昆明")
    e1.data.location = "POLLUTED"        # 调用方改写不应回流缓存
    e2 = client.geocode("昆明")          # 命中路径
    assert e2.data.location == "102.7,25.0"
    assert e1 is not e2                  # 两份独立对象（miss 命中同规矩）


def test_cache_thread_safe_under_double_fire(monkeypatch):
    """并发安全：线程池四发同参不抛异常；miss race 至多双发，后续全命中。"""
    from concurrent.futures import ThreadPoolExecutor
    n = {"get": 0}

    def fake_get(path, params):
        n["get"] += 1
        return _geo_payload(params["address"], "102.7,25.0")

    client = AMapClient(api_key="k")
    monkeypatch.setattr(client, "_get", fake_get)
    with ThreadPoolExecutor(max_workers=2) as pool:
        envs = list(pool.map(lambda _: client.geocode("昆明"), range(4)))
    assert all(e.ok and e.data.location == "102.7,25.0" for e in envs)
    assert n["get"] <= 2                # 竞态窗口至多双发，稳定后全命中
    assert client.stats["cache_hit"] >= 2
