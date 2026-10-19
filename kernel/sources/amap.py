"""高德开放平台 Web 服务 REST 客户端 — 地理空间底座六原语（PoC，docs/plans amap §3）。

与 FlyAI 的分工：FlyAI 强在旅行供给（海外+港澳台），高德强在地理空间底座
（国内村级 POI / 行政区划 / 公交路线 / 天气）。``amap_no_result``（常见于海外
地名 geocode 落空）时说明定位缺口；飞猪只可补充旅行商品供给。

传输层用 urllib（kernel 只依赖标准库 + pydantic 的约束不破）；出网前必检
``api_key``，无 Key 直接 ``amap_invalid_key`` 包络，不出网。

读缓存（PoC2 §4.1 C1）：实例级 TTL 缓存六原语成功包络（行政区划 24h /
天气 1h / 检索与公交 10min），键含方法名与排序参数；证据保留实际取数时间与命中标记。
"""
import functools
import inspect
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from fieldwork_agent.kernel.models import (
    Envelope,
    GeoLocation,
    ItemPage,
    Schedule,
    SearchItem,
    ToolError,
)
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from fieldwork_agent.kernel.travel import finite_price, stamp_evidence
from pydantic import BaseModel, Field

BASE_URL = "https://restapi.amap.com"
_TIMEOUT = 10  # REST 单查轻量，比 flyai CLI 的 30s 更紧

# 读缓存 TTL 分档（PoC2 §4.1 C1）：按数据稳定性分档；transit 窗口短于班次变动粒度
_TTL_STABLE = 24 * 3600   # geocode/regeocode：行政区划稳定
_TTL_WEATHER = 3600       # weather：1h
_TTL_VOLATILE = 600       # text/around/transit：10min

# infocode → 内核错误码（官方错误码表钉死三枚，其余兜底 amap_error）
_INFOCODE_MAP = {
    "10001": "amap_invalid_key",
    "10003": "amap_quota",
    "10004": "amap_rate_limited",
}

_HINTS = {
    "amap_invalid_key": (
        "请到 https://console.amap.com 申请『Web 服务』类型 Key，"
        "配置 AMAP_API_KEY 环境变量或 ~/.fieldwork_agent/config.json 的 amap_api_key。"),
    "amap_quota": (
        "高德日配额已用尽（infocode 10003）：次日自动恢复；"
        "个人认证可提额，或降低调用频率。"),
    "amap_rate_limited": "触发高德 QPS 限流（infocode 10004）：请稍后重试并降低并发。",
    "amap_no_result": (
        "高德仅覆盖境内：境外田野点的定位/周边/接驳功能正在开发中"
        "（后续版本将追加）；境内生僻地名请改用标准写法（省+市+乡镇）；"
        "飞猪仅可补充交通/住宿商品供给，不能替代地理位置或设施事实；位置请补充标准地址或坐标。"),
    "amap_timeout": "高德接口超时：请稍后重试。",
    "parse_error": "高德返回了非 JSON 内容：请重试；持续出现请查看 https://lbs.amap.com 服务公告。",
    "amap_error": "高德服务异常：请到 https://lbs.amap.com 查看错误码文档排查。",
}


def _error_envelope(message: str, code: str = "") -> Envelope:
    """本地失败（传输/解析）按内容归类——与 flyai._error_envelope 同一模式。"""
    low = message.lower()
    if not code:
        if "timeout" in low or "timed out" in low:
            code = "amap_timeout"
        elif "json" in low:
            code = "parse_error"
        else:
            code = "amap_error"
    return Envelope(ok=False, error=ToolError(
        code=code, message=message or "高德调用失败", hint=_HINTS.get(code, _HINTS["amap_error"])))


def _infocode_envelope(infocode: str, info: str) -> Envelope:
    """服务器应答 infocode != 10000：按钉死映射折包络，message 带 infocode+info。"""
    code = _INFOCODE_MAP.get(infocode, "amap_error")
    message = f"高德 infocode {infocode}: {info}" if info else f"高德 infocode {infocode}"
    return Envelope(ok=False, error=ToolError(
        code=code, message=message, hint=_HINTS[code]))


def _no_result_envelope() -> Envelope:
    return Envelope(ok=False, error=ToolError(
        code="amap_no_result", message="高德未收录该地点或无匹配数据",
        hint=_HINTS["amap_no_result"]))


def _first(value: Any) -> str:
    """v3 行政区字段可能是 str / [str] / [] / None：统一拍平为 str。"""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    if value is None:
        return ""
    return str(value)


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return finite_price(value)
    except (TypeError, ValueError):
        return None


def _cached(ttl_seconds: float):
    """方法级实例读缓存（PoC2 §4.1 C1）：键=(方法名, 排序参数)，值=(expire_at, envelope)。

    位置/关键字混合调用先 bind + apply_defaults 归一（同语义调用同键）；
    只缓存 ok 包络（失败穿透：限流/配额等瞬态错误不放大）；出网在锁外
    （site_profile 线程池并发不被串行化）；缓存存 fn 产物、命中返回其深
    拷贝——交给调用方的对象永不与缓存共享（防下游改写污染）。
    """

    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            bound = sig.bind(self, *args, **kwargs)
            bound.apply_defaults()
            params = {k: v for k, v in bound.arguments.items() if k != "self"}
            key = (fn.__name__, tuple(sorted(params.items())))
            with self._cache_lock:
                hit = self._cache.get(key)
                if hit is not None and hit[0] > time.monotonic():
                    self.stats["cache_hit"] += 1
                    return stamp_evidence(hit[1].model_copy(deep=True), "amap",
                                          {"method": fn.__name__, **params}, cache_hit=True)
            env = fn(self, *args, **kwargs)
            if env.ok:
                stamp_evidence(env, "amap", {"method": fn.__name__, **params})
                with self._cache_lock:
                    self._cache[key] = (time.monotonic() + ttl_seconds, env)
                return env.model_copy(deep=True)
            return env
        return wrapper
    return deco


class AMapClient:
    """高德 v3 REST 客户端（实例持 TTL 读缓存与命中计数，跨线程可复用；
    api_key 由宿主从 Config 注入；缓存仅进程内，不落盘不跨实例）。"""

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.stats = {"cache_hit": 0}   # 宿主日志可见，验证省配额（§4.1）
        self._cache: dict[tuple, tuple[float, Envelope]] = {}
        self._cache_lock = threading.Lock()

    # ══════════════════════════════════════════
    # 传输层
    # ══════════════════════════════════════════

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET 一枚 v3 接口并解析 JSON；本地失败以 {"infocode": "00000", "info": ...} 上报
        （与 flyai 的 {"status": -1, "error": ...} 同一降级约定，不向上抛异常）。"""
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
        except TimeoutError:
            return {"infocode": "00000", "info": "timeout: timed out"}
        except json.JSONDecodeError as e:
            return {"infocode": "00000", "info": f"JSON parse error: {e}"}
        except (urllib.error.URLError, OSError) as e:
            return {"infocode": "00000", "info": f"urlopen error: {e}"}

    def _call(self, path: str, params: dict[str, Any]) -> tuple[dict[str, Any] | None, Envelope | None]:
        """统一前置：无 Key 短路不出网；成功返回 (payload, None)，失败 (None, 错误包络)。"""
        if not self.api_key:
            return None, _error_envelope(
                "未配置高德 API Key（AMAP_API_KEY / amap_api_key）", code="amap_invalid_key")
        merged = dict(params)
        merged["key"] = self.api_key
        payload = self._get(path, merged)
        infocode = str(payload.get("infocode", ""))
        if infocode == "00000":            # 本地失败（传输/解析）
            return None, _error_envelope(str(payload.get("info", "")))
        if infocode != "10000":            # 服务器应答错误
            return None, _infocode_envelope(infocode, str(payload.get("info", "")))
        return payload, None

    # ══════════════════════════════════════════
    # 六原语
    # ══════════════════════════════════════════

    @_cached(_TTL_STABLE)
    def geocode(self, address: str, city: str = "") -> Envelope:
        """地理编码：地名 → GeoLocation（画像地理段/末端接驳的锚点）。"""
        params: dict[str, Any] = {"address": address}
        if city:
            params["city"] = city
        payload, err = self._call("/v3/geocode/geo", params)
        if err:
            return err
        geos = payload.get("geocodes") or []
        if not geos:
            return _no_result_envelope()
        g = geos[0]
        return Envelope(ok=True, data=GeoLocation(
            location=_first(g.get("location")),
            province=_first(g.get("province")),
            city=_first(g.get("city")),
            district=_first(g.get("district")),
            township=_first(g.get("township")),
            adcode=_first(g.get("adcode")),
            formatted_address=_first(g.get("formatted_address")),
            level=_first(g.get("level")),
            candidates=[{k: _first(candidate.get(k)) for k in
                         ("location", "province", "city", "district", "adcode", "formatted_address", "level")}
                        for candidate in geos if isinstance(candidate, dict)],
            ambiguous=len(geos) > 1,
            source="amap"))

    @_cached(_TTL_STABLE)
    def regeocode(self, location: str) -> Envelope:
        """逆地理编码："lng,lat" → GeoLocation（报道人/样点坐标落到行政区的反向锚定）。"""
        payload, err = self._call("/v3/geocode/regeo", {"location": location})
        if err:
            return err
        regeo = payload.get("regeocode") or {}
        comp = regeo.get("addressComponent") or {}
        return Envelope(ok=True, data=GeoLocation(
            location=location,                      # 响应无此键：请求坐标回填
            province=_first(comp.get("province")),
            city=_first(comp.get("city")),
            district=_first(comp.get("district")),
            township=_first(comp.get("township")),
            adcode=_first(comp.get("adcode")),
            formatted_address=_first(regeo.get("formatted_address")),
            source="amap"))

    @_cached(_TTL_VOLATILE)
    def text_search(self, keywords: str, city: str = "", page_size: int = 10) -> Envelope:
        """关键词 POI 搜索：按行政区找目标（卫生院/派出所/集贸市场等田野保障设施）。"""
        params: dict[str, Any] = {"keywords": keywords, "offset": page_size}
        if city:
            params["city"] = city
        payload, err = self._call("/v3/place/text", params)
        if err:
            return err
        return _pois_page(payload)

    @_cached(_TTL_VOLATILE)
    def around_search(self, keywords: str, location: str, radius: int = 5000,
                      page_size: int = 10) -> Envelope:
        """周边搜索：以 "lng,lat" 为圆心找半径内设施（驻村便利性画像）。"""
        params: dict[str, Any] = {"keywords": keywords, "location": location,
                                  "radius": radius, "offset": page_size}
        payload, err = self._call("/v3/place/around", params)
        if err:
            return err
        return _pois_page(payload)

    @_cached(_TTL_VOLATILE)
    def direction_transit(self, origin: str, destination: str,
                          city: str = "", cityd: str = "",
                          date: str = "") -> Envelope:
        """公交路径规划：枢纽→田野点的末端接驳方案（每条 transit 归一为一个条目）。

        v3 语义（M4 修正）：``city``=出发城市、``cityd``=到达城市（跨城必填
        cityd）；只给 ``city`` 时双填兼容（同城场景，M1 行为不变）。
        ``date``（N1）=班次日期 YYYY-MM-DD，v3 仅支持 3 天内——窗口守卫在
        聚合层 ``plan_last_mile``；空串不进 query（现有调用零影响）。
        """
        params: dict[str, Any] = {"origin": origin, "destination": destination}
        if city:
            params["city"] = city
            params["cityd"] = cityd or city
        elif cityd:
            params["cityd"] = cityd
        if date:
            params["date"] = date
        payload, err = self._call("/v3/direction/transit/integrated", params)
        if err:
            return err
        return _transits_page(payload)

    @_cached(_TTL_WEATHER)
    def weather(self, city: str, extensions: str = "all") -> Envelope:
        """天气：以实际 casts.date 为覆盖范围，保留 reporttime，不硬编码预报天数。"""
        payload, err = self._call("/v3/weather/weatherInfo",
                                  {"city": city, "extensions": extensions})
        if err:
            return err
        if extensions == "base":
            lives = payload.get("lives") or []
            if not lives:
                return _no_result_envelope()
            return Envelope(ok=True, data=lives)
        casts: list[dict[str, Any]] = []
        for forecast in payload.get("forecasts") or []:
            casts.extend({**cast, "reporttime": forecast.get("reporttime", ""),
                          "adcode": forecast.get("adcode", ""), "city": forecast.get("city", "")}
                         for cast in forecast.get("casts") or [] if isinstance(cast, dict))
        if not casts:
            return _no_result_envelope()
        return Envelope(ok=True, data=casts)


# ════════════════════════════════════════════════════════════════════
# 归一化助手
# ════════════════════════════════════════════════════════════════════


def _pois_page(payload: dict[str, Any]) -> Envelope:
    """pois → ItemPage（无价格条目：price=None，地址/坐标/电话/距离进 extra）。"""
    pois = payload.get("pois") or []
    items = [SearchItem(name=str(p.get("name", "")),
                        extra={k: v for k, v in p.items()
                               if k != "name" and v not in (None, "", [])})
             for p in pois]
    total = _to_int(payload.get("count"))
    return Envelope(ok=True, data=ItemPage(items=items, total=total if total else len(items)))


def _transits_page(payload: dict[str, Any]) -> Envelope:
    """transits → ItemPage：name=公交线名拼接，price=cost 元，duration 折分钟入 Schedule。"""
    transits = (payload.get("route") or {}).get("transits") or []
    items: list[SearchItem] = []
    for t in transits:
        lines: list[str] = []
        for seg in t.get("segments") or []:
            for line in (seg.get("bus") or {}).get("buslines") or []:
                name = line.get("name")
                if name:
                    lines.append(str(name))
        duration = _to_int(t.get("duration"))
        items.append(SearchItem(
            name=" → ".join(lines) or "公交方案",
            price=_to_float(t.get("cost")),
            extra={"walking_distance": _to_int(t.get("walking_distance")),
                   "origin": (payload.get("route") or {}).get("origin", ""),
                   "destination": (payload.get("route") or {}).get("destination", ""),
                   "segments": t.get("segments") or []},
            schedule=Schedule(
                duration_minutes=duration // 60 if duration is not None else None,
                stops=max(len(lines) - 1, 0), stops_kind="transfers",
                segments=t.get("segments") or [])))
    prices = [i.price for i in items if i.price is not None]
    return Envelope(ok=True, data=ItemPage(
        items=items, total=len(items),
        cheapest=min(prices) if prices else None,
        dearest=max(prices) if prices else None))


# ════════════════════════════════════════════════════════════════════
# 能力注册 —— 6 地理原语进 registry（schema 单一来源，与 flyai 同一模式）
# ════════════════════════════════════════════════════════════════════


def _make_amap_fn(method_name: str):
    """生成地理能力的 capability fn：注入 amap_client（宿主自持 AMapClient 实例，
    键名与 flyai 的 client 分道防串味），未注入时自建 ``AMapClient()``。
    capability 名 = ``amap_`` + 方法名。"""

    def fn(*, amap_client: AMapClient | None = None, **kwargs: Any) -> Envelope:
        target = amap_client if amap_client is not None else AMapClient()
        return getattr(target, method_name)(**kwargs)

    fn.__name__ = f"amap_{method_name}"
    fn.__qualname__ = f"amap_{method_name}"
    return fn


amap_geocode = _make_amap_fn("geocode")
amap_regeocode = _make_amap_fn("regeocode")
amap_text_search = _make_amap_fn("text_search")
amap_around_search = _make_amap_fn("around_search")
amap_direction_transit = _make_amap_fn("direction_transit")
amap_weather = _make_amap_fn("weather")


class AmapGeocodeArgs(BaseModel):
    """``amap_geocode`` 入参（与 AMapClient.geocode 形参对齐）。"""

    address: str = Field(description="结构化地址或地名（如 喜洲镇）")
    city: str = Field(default="", description="指定城市（名称或 adcode，缩小歧义）")


class AmapRegeocodeArgs(BaseModel):
    """``amap_regeocode`` 入参。"""

    location: str = Field(description="经纬度串 lng,lat")


class AmapTextSearchArgs(BaseModel):
    """``amap_text_search`` 入参。"""

    keywords: str = Field(description="关键词（如 卫生院|派出所|集贸市场）")
    city: str = Field(default="", description="城市名或 adcode")
    page_size: int = Field(default=10, description="每页条数")


class AmapAroundSearchArgs(BaseModel):
    """``amap_around_search`` 入参。"""

    keywords: str = Field(description="关键词")
    location: str = Field(description="圆心经纬度 lng,lat")
    radius: int = Field(default=5000, description="搜索半径（米）")
    page_size: int = Field(default=10, description="每页条数")


class AmapDirectionTransitArgs(BaseModel):
    """``amap_direction_transit`` 入参。"""

    origin: str = Field(description="出发坐标 lng,lat")
    destination: str = Field(description="到达坐标 lng,lat")
    city: str = Field(default="", description="出发城市名（v3：跨城与 cityd 分传）")
    cityd: str = Field(default="", description="到达城市名（同城可省；跨城必填）")


class AmapWeatherArgs(BaseModel):
    """``amap_weather`` 入参。"""

    city: str = Field(description="城市名或 adcode")
    extensions: str = Field(default="all", description="all=4日预报 / base=实况")


register(CapabilityMeta(
    name="amap_geocode",
    description="高德地理编码：地名→坐标与行政区划（省市区镇/adcode），田野点画像与末端接驳的地理锚点；"
                "海外地名会返回 amap_no_result，此时应改用 FlyAI 搜索。",
    fn=amap_geocode, args_model=AmapGeocodeArgs,
    read_only=True, open_world=True))
register(CapabilityMeta(
    name="amap_regeocode",
    description="高德逆地理编码：坐标→行政区划与标准地址，用于把报道人/样点坐标落到行政区。",
    fn=amap_regeocode, args_model=AmapRegeocodeArgs,
    read_only=True, open_world=True))
register(CapabilityMeta(
    name="amap_text_search",
    description="高德关键词 POI 搜索：按行政区检索卫生院/派出所/银行/集贸市场等田野保障设施。",
    fn=amap_text_search, args_model=AmapTextSearchArgs,
    read_only=True, open_world=True))
register(CapabilityMeta(
    name="amap_around_search",
    description="高德周边搜索：以田野点坐标为圆心检索半径内设施（如 5km 内诊所/快递点），驻村便利性画像。",
    fn=amap_around_search, args_model=AmapAroundSearchArgs,
    read_only=True, open_world=True))
register(CapabilityMeta(
    name="amap_direction_transit",
    description="高德公交路径规划：枢纽站→田野点的末端接驳方案（线路拼接/票价/耗时，每条方案一个条目）。",
    fn=amap_direction_transit, args_model=AmapDirectionTransitArgs,
    read_only=True, open_world=True))
register(CapabilityMeta(
    name="amap_weather",
    description="高德天气查询：田野点 4 日预报或实时天气（adcode 锚定），行前气候提示与衣物/装备建议。",
    fn=amap_weather, args_model=AmapWeatherArgs,
    read_only=True, open_world=True))
