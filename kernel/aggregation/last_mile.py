"""枢纽→田野点末端接驳聚合（PoC §5.2，M4）。

``plan_last_mile``：两次 geocode 正算枢纽城市与田野点坐标 → v3 跨城公交
路径规划（``city``=出发市、``cityd``=到达市），返回两端正算结果与接驳方案页。约定：

- 严格无状态：``amap_client`` keyword-only 注入，默认 None 时自建；
- 任一端 geocode 失败（含境外地名 ``amap_no_result`` 或调用异常）→ 统一
  ``amap_no_result`` 失败包络（message 指明哪一端，hint 明示境外接驳缺口
  并引导标准地名），失败即止不发 transit；无 Key（``amap_invalid_key``）例外：
  原样透传配 Key 指引，不折 no_result（防默认无 Key 部署下 LLM 按换地名 hint 空转）；
- transit 失败（无方案/服务不可用）不算整体失败：``ok=True`` + 空 ``options``
  + ``degraded`` 附言（末端接驳查不到 ≠ 出行不可行，建议班车/包车等公路方式）；
- ``date_hint`` 透传（N1）：合法 YYYY-MM-DD 且 3 天内（含当日）→ v3 ``date``
  班次日期；非法/超窗 → 忽略 + ``degraded`` 附言（调研行程常为未来数周，
  透传是增强非承诺）；
- ``compose_itinerary`` 行程级整合（leg 模型、预算联动）留给 PoC2，本能力即其数据探针。
"""
from datetime import date, datetime, timedelta

from fieldwork_agent.kernel.aggregation.amap_refs import live_geocode_ref, live_transit_ref
from fieldwork_agent.kernel.models import Envelope, GeoLocation, ItemPage, ToolError
from fieldwork_agent.kernel.registry import CapabilityMeta, register
from fieldwork_agent.kernel.source_requests import source_task
from fieldwork_agent.kernel.sources.amap import AMapClient
from pydantic import BaseModel, Field

_NO_RESULT_HINT = ("高德仅覆盖境内：境外田野点的末端接驳功能正在开发中"
                   "（后续版本将追加）；境内生僻地名请改用标准写法（省+市+乡镇）；"
                   "可暂用搜索工具了解交通。")


class PlanLastMileArgs(BaseModel):
    """``plan_last_mile`` 的入参模型（``amap_client`` 注入参数不进 schema）。"""

    hub: str = Field(description="枢纽城市名（如 昆明）")
    site: str = Field(description="田野点地名（如 喜洲）")
    date_hint: str = Field(default="",
                           description="出行日期 YYYY-MM-DD（可选；高德班次仅支持 3 天内，"
                                       "超窗自动按当日/实时班次查询并附言说明）")


def _no_result(message: str) -> Envelope:
    """geocode 失败/异常的统一出口：amap_no_result 包络（双源回退信号）。"""
    return Envelope(ok=False, error=ToolError(
        code="amap_no_result", message=message, hint=_NO_RESULT_HINT))


def _resolve_date_param(date_hint: str) -> tuple[str, str | None]:
    """date_hint → (v3 date 参数, 降级附言)；空串零打扰。

    v3 班次日期仅支持 3 天内（含当日），而调研行程常为未来数周——透传是
    增强非承诺：仅合法且在窗内才透传（isoformat 规范零填充）；非法格式/
    超窗一律忽略并附言说明（对照 itinerary 日期铁律：不拼串、不崩）。
    """
    if not date_hint:
        return "", None
    try:
        d = datetime.strptime(date_hint, "%Y-%m-%d").date()
    except ValueError:
        return "", (f"出行日期提示 {date_hint} 无法解析（需 YYYY-MM-DD），"
                    "已按当日/实时班次查询")
    today = date.today()
    if not (today <= d <= today + timedelta(days=2)):
        return "", "高德班次日期仅支持 3 天内，已按当日/实时班次查询"
    return d.isoformat(), None


@source_task
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
        if hub_env.error is not None and hub_env.error.code in ("amap_invalid_key", "amap_ip_denied", "amap_quota", "amap_rate_limited"):
            return Envelope(ok=False, error=hub_env.error)  # 无 Key 原样透传
        return _no_result(f"枢纽地名未能解析：{hub}")

    try:
        site_env = amap_client.geocode(site)
    except Exception as exc:
        return _no_result(f"田野点地名解析异常：{site}（{exc}）")
    if not (site_env.ok and isinstance(site_env.data, GeoLocation)):
        if site_env.error is not None and site_env.error.code in ("amap_invalid_key", "amap_ip_denied", "amap_quota", "amap_rate_limited"):
            return Envelope(ok=False, error=site_env.error)  # 无 Key 原样透传
        return _no_result(f"田野点地名未能解析：{site}")

    hub_geo, site_geo = hub_env.data, site_env.data
    date_param, date_note = _resolve_date_param(date_hint)
    # v3 跨城语义：city=出发城市、cityd=到达城市（同城时两者同值亦兼容）
    transit_env = amap_client.direction_transit(
        origin=hub_geo.location, destination=site_geo.location,
        city=hub_geo.city, cityd=site_geo.city, date=date_param)
    # N5 §4.3 C3：geocode×2 走到此处必成功恒附；transit 键只在调用成功时附
    live_refs = [live_geocode_ref(hub), live_geocode_ref(site)]
    if transit_env.ok and isinstance(transit_env.data, ItemPage):
        options, degraded = transit_env.data, None
        live_refs.append(
            live_transit_ref(hub_geo.location, site_geo.location, date_param))
    else:
        options = ItemPage(items=[], total=0)
        degraded = "末端公交接驳暂不可查（无方案或服务不可用），可考虑城际班车/包车等公路方式"
    if date_note:  # date 降级附言与 transit 失败附言同时发生时合并呈现
        degraded = date_note if degraded is None else f"{degraded}；{date_note}"
    return Envelope(ok=True,
                    data={"hub": hub_geo, "site": site_geo, "options": options,
                          "live_refs": live_refs},
                    degraded=degraded)


register(CapabilityMeta(
    name="plan_last_mile",
    description="聚合末端接驳：两次地理编码正算枢纽城市与田野点坐标，再查跨城公交方案"
                "（city=出发市/cityd=到达市），返回两端正算结果（五级行政区划+坐标）与"
                "接驳方案页（线路拼接/票价/耗时）；任一端地名未收录返回 amap_no_result"
                "（hint 引导标准地名），未配高德 Key 则透传 amap_invalid_key 配 Key 指引；"
                "接驳查不到不判失败（degraded 建议公路方式）。",
    fn=plan_last_mile,
    args_model=PlanLastMileArgs,
    read_only=True, open_world=True))
