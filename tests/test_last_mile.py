"""kernel/aggregation/last_mile — plan_last_mile 枢纽→田野点末端接驳聚合（PoC §5.2，M4）。

Stub 替身零网络：geocode 按地名分岔（昆明=枢纽 / 喜洲=田野点），
direction_transit 记录入参返回固定方案页。断言三组语义：

- 成功路径：transit 用两端正算坐标，v3 跨城语义 city=出发市、cityd=到达市；
- 任一端 geocode 失败（amap_no_result 或抛异常）→ 统一 amap_no_result
  失败包络（message 指明哪一端），且不再发 transit；
- transit 失败 → ok=True + 空 options + degraded（末端接驳查不到不算整体失败）。
"""
from fieldwork_agent.kernel.aggregation.last_mile import plan_last_mile
from fieldwork_agent.kernel.models import (
    Envelope,
    GeoLocation,
    ItemPage,
    Schedule,
    SearchItem,
    ToolError,
)
from fieldwork_agent.kernel.registry import build_tool_schemas

# 两端固定正算结果（坐标用真实昆明/喜洲经纬度，断言 transit 参数透传）
_GEO = {
    "昆明": {"location": "102.7122,25.0389", "city": "昆明市",
             "district": "五华区", "adcode": "530102"},
    "喜洲": {"location": "100.1963,25.0473", "city": "大理白族自治州",
             "district": "大理市", "adcode": "532901"},
}


class StubTransitClient:
    """geocode 按地名分岔；direction_transit 记录入参返回单方案页。

    fail_addresses 中的地名 geocode 折 amap_no_result；raise_on 中的抛异常
    （验证聚合层异常折算）；fail_transit 使 direction_transit 返回失败包络。
    """

    def __init__(self, *, fail_addresses=(), raise_on=(), fail_transit=False):
        self.calls: list[tuple[str, dict]] = []
        self.fail_addresses = set(fail_addresses)
        self.raise_on = set(raise_on)
        self.fail_transit = fail_transit

    def geocode(self, address: str, city: str = "") -> Envelope:
        self.calls.append(("geocode", {"address": address}))
        if address in self.raise_on:
            raise RuntimeError(f"网络故障：{address}")
        if address in self.fail_addresses or address not in _GEO:
            return Envelope(ok=False, error=ToolError(
                code="amap_no_result", message=f"地名未收录：{address}",
                hint="换标准地名"))
        g = _GEO[address]
        return Envelope(ok=True, data=GeoLocation(
            location=g["location"], province="云南省", city=g["city"],
            district=g["district"], township="", adcode=g["adcode"],
            formatted_address=f"云南省{g['city']}{g['district']}", source="amap"))

    def direction_transit(self, origin: str, destination: str,
                          city: str = "", cityd: str = "") -> Envelope:
        self.calls.append(("direction_transit", {"origin": origin,
                                                 "destination": destination,
                                                 "city": city, "cityd": cityd}))
        if self.fail_transit:
            return Envelope(ok=False, error=ToolError(
                code="amap_error", message="服务不可用", hint="稍后重试"))
        return Envelope(ok=True, data=ItemPage(
            items=[SearchItem(name="C71 → 喜洲班车", price=68.0,
                              schedule=Schedule(duration_minutes=240, stops=1))],
            total=1, cheapest=68.0, dearest=68.0))


# ---------------------------------------------------------------- 成功路径


def test_plan_last_mile_success_cross_city_transit_params():
    """两端正算成功：transit 收两端正算坐标，city=出发市/cityd=到达市（v3 跨城）。"""
    geo = StubTransitClient()
    env = plan_last_mile("昆明", "喜洲", amap_client=geo)
    assert env.ok is True and env.degraded is None
    assert env.data["hub"].city == "昆明市" and env.data["hub"].adcode == "530102"
    assert env.data["site"].city == "大理白族自治州"
    assert isinstance(env.data["options"], ItemPage) and env.data["options"].total == 1
    assert [c[0] for c in geo.calls] == ["geocode", "geocode", "direction_transit"]
    transit = geo.calls[2][1]
    assert transit["origin"] == "102.7122,25.0389"
    assert transit["destination"] == "100.1963,25.0473"
    assert transit["city"] == "昆明市"
    assert transit["cityd"] == "大理白族自治州"


# ---------------------------------------------------------------- geocode 失败


def test_hub_geocode_fail_returns_amap_no_result_without_transit():
    """枢纽端解析失败 → amap_no_result 包络指明"枢纽"，失败即止不发 transit。"""
    geo = StubTransitClient(fail_addresses={"昆明"})
    env = plan_last_mile("昆明", "喜洲", amap_client=geo)
    assert env.ok is False and env.error.code == "amap_no_result"
    assert "枢纽" in env.error.message and env.error.hint
    assert [c[0] for c in geo.calls] == ["geocode"]


def test_site_geocode_fail_returns_amap_no_result_without_transit():
    """田野点端解析失败同样折 amap_no_result（message 指明"田野点"）。"""
    geo = StubTransitClient(fail_addresses={"喜洲"})
    env = plan_last_mile("昆明", "喜洲", amap_client=geo)
    assert env.ok is False and env.error.code == "amap_no_result"
    assert "田野点" in env.error.message
    assert [c[0] for c in geo.calls] == ["geocode", "geocode"]


def test_geocode_exception_folds_to_amap_no_result():
    """geocode 抛异常同样折 amap_no_result 包络，不裸抛（对 site 端验证）。"""
    geo = StubTransitClient(raise_on={"喜洲"})
    env = plan_last_mile("昆明", "喜洲", amap_client=geo)
    assert env.ok is False and env.error.code == "amap_no_result"
    assert [c[0] for c in geo.calls] == ["geocode", "geocode"]


# ---------------------------------------------------------------- 无 Key 透传


_KEY_ERR = ToolError(
    code="amap_invalid_key",
    message="未配置高德 API Key（AMAP_API_KEY / amap_api_key）",
    hint="请到 https://console.amap.com 申请『Web 服务』类型 Key，"
         "配置 AMAP_API_KEY 环境变量或 config.json 的 amap_api_key")


def test_invalid_key_passthrough_not_no_result():
    """无 Key（amap_invalid_key）原样透传配 Key 指引，不折 amap_no_result。

    默认无 AMAP_API_KEY 的部署下这是确定性首路径：折成 no_result 会让 LLM
    按"换标准地名"hint 空转重试（评审 M1）。
    """
    class NoKeyClient(StubTransitClient):
        def geocode(self, address: str, city: str = "") -> Envelope:
            self.calls.append(("geocode", {"address": address}))
            return Envelope(ok=False, error=_KEY_ERR)

    geo = NoKeyClient()
    env = plan_last_mile("昆明", "喜洲", amap_client=geo)
    assert env.ok is False
    assert env.error is not None and env.error.code == "amap_invalid_key"
    assert env.error == _KEY_ERR                    # 原样透传（message/hint 不折算）
    assert [c[0] for c in geo.calls] == ["geocode"]  # 失败即止不发 transit


def test_site_end_invalid_key_also_passthrough():
    """site 端 geocode 无 Key 同样透传（两端分支对称）。"""
    class SiteNoKeyClient(StubTransitClient):
        def geocode(self, address: str, city: str = "") -> Envelope:
            if address == "喜洲":
                self.calls.append(("geocode", {"address": address}))
                return Envelope(ok=False, error=_KEY_ERR)
            return super().geocode(address, city)

    geo = SiteNoKeyClient()
    env = plan_last_mile("昆明", "喜洲", amap_client=geo)
    assert env.ok is False
    assert env.error is not None and env.error == _KEY_ERR
    assert [c[0] for c in geo.calls] == ["geocode", "geocode"]


# ---------------------------------------------------------------- transit 失败降级


def test_transit_failure_degrades_to_empty_options():
    """接驳查不到不算整体失败：ok=True + 空 options + degraded 建议公路方式。"""
    geo = StubTransitClient(fail_transit=True)
    env = plan_last_mile("昆明", "喜洲", amap_client=geo)
    assert env.ok is True
    assert isinstance(env.data["options"], ItemPage) and env.data["options"].total == 0
    assert env.degraded and "公路" in env.degraded
    assert env.data["hub"].city == "昆明市"  # 两端正算结果照常返回


# ---------------------------------------------------------------- 注册面


def test_plan_last_mile_registered_in_schemas():
    """注册为聚合能力：hub/site/date_hint 进 schema，注入参数 amap_client 不进。"""
    tools = {t["function"]["name"]: t for t in build_tool_schemas()}
    assert "plan_last_mile" in tools
    props = tools["plan_last_mile"]["function"]["parameters"]["properties"]
    assert set(props) == {"hub", "site", "date_hint"}
    assert "amap_client" not in props
