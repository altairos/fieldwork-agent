"""枢纽→田野点末端接驳聚合（PoC §5.2，M4）。

``plan_last_mile``：两次 geocode 正算枢纽城市与田野点坐标 → v3 跨城公交
路径规划（``city``=出发市、``cityd``=到达市），返回两端正算结果与接驳方案页。约定：

- 严格无状态：``amap_client`` keyword-only 注入，默认 None 时自建；
- 任一端 geocode 失败（含海外地名 ``amap_no_result`` 或调用异常）→ 统一
  ``amap_no_result`` 失败包络（message 指明哪一端，hint 引导标准地名），
  失败即止不发 transit；
- transit 失败（无方案/服务不可用）不算整体失败：``ok=True`` + 空 ``options``
  + ``degraded`` 附言（末端接驳查不到 ≠ 出行不可行，建议班车/包车等公路方式）；
- ``date_hint`` 占位入参：M4 不透传 v3 ``date``（班次日期过滤留给 PoC2）；
- ``compose_itinerary`` 行程级整合（leg 模型、预算联动）留给 PoC2，本能力即其数据探针。
"""
from fieldwork_agent.kernel.models import Envelope, GeoLocation, ItemPage, ToolError
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from fieldwork_agent.kernel.sources.amap import AMapClient
from pydantic import BaseModel, Field

_NO_RESULT_HINT = "请改用标准地名（省+市+乡镇）；海外地名高德不收录，可改用搜索工具。"


class PlanLastMileArgs(BaseModel):
    """``plan_last_mile`` 的入参模型（``amap_client`` 注入参数不进 schema）。"""

    hub: str = Field(description="枢纽城市名（如 昆明）")
    site: str = Field(description="田野点地名（如 喜洲）")
    date_hint: str = Field(default="", description="出行日期提示（占位：PoC 不透传班次日期）")


def _no_result(message: str) -> Envelope:
    """geocode 失败/异常的统一出口：amap_no_result 包络（双源回退信号）。"""
    return Envelope(ok=False, error=ToolError(
        code="amap_no_result", message=message, hint=_NO_RESULT_HINT))


def plan_last_mile(hub: str, site: str, date_hint: str = "", *,
                   amap_client: AMapClient | None = None) -> Envelope:
    """聚合枢纽城市→田野点的末端接驳方案（跨城公交为主，PoC §5.2）。"""
    if amap_client is None:
        amap_client = AMapClient()

    try:
        hub_env = amap_client.geocode(hub)
    except Exception as exc:
        return _no_result(f"枢纽地名解析异常：{hub}（{exc}）")
    if not (hub_env.ok and isinstance(hub_env.data, GeoLocation)):
        return _no_result(f"枢纽地名未能解析：{hub}")

    try:
        site_env = amap_client.geocode(site)
    except Exception as exc:
        return _no_result(f"田野点地名解析异常：{site}（{exc}）")
    if not (site_env.ok and isinstance(site_env.data, GeoLocation)):
        return _no_result(f"田野点地名未能解析：{site}")

    hub_geo, site_geo = hub_env.data, site_env.data
    # v3 跨城语义：city=出发城市、cityd=到达城市（同城时两者同值亦兼容）
    transit_env = amap_client.direction_transit(
        origin=hub_geo.location, destination=site_geo.location,
        city=hub_geo.city, cityd=site_geo.city)
    if transit_env.ok and isinstance(transit_env.data, ItemPage):
        options, degraded = transit_env.data, None
    else:
        options = ItemPage(items=[], total=0)
        degraded = "末端公交接驳暂不可查（无方案或服务不可用），可考虑城际班车/包车等公路方式"
    return Envelope(ok=True,
                    data={"hub": hub_geo, "site": site_geo, "options": options},
                    degraded=degraded)


register(CapabilityMeta(
    name="plan_last_mile",
    description="聚合末端接驳：两次地理编码正算枢纽城市与田野点坐标，再查跨城公交方案"
                "（city=出发市/cityd=到达市），返回两端正算结果（五级行政区划+坐标）与"
                "接驳方案页（线路拼接/票价/耗时）；任一端地名未收录返回 amap_no_result"
                "（hint 引导标准地名）；接驳查不到不判失败（degraded 建议公路方式）。",
    fn=plan_last_mile,
    args_model=PlanLastMileArgs,
    read_only=True, open_world=True))
